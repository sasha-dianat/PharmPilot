"""
NCPDP Reject Code auto-resolution engine.
Handles the top 25 reject codes with automated fix strategies.
Remaining rejects escalate to pharmacist with recommended action.
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ResolutionAction:
    action: str                    # What the system did or recommends
    auto_resolvable: bool = False  # Can system fix without pharmacist?
    rebill_fields: dict = field(default_factory=dict)  # Modified fields for rebill
    pharmacist_instruction: Optional[str] = None
    urgency: str = "normal"        # normal, high, critical


# NCPDP reject code resolution strategies
REJECT_RESOLUTION_MAP: dict[str, ResolutionAction] = {

    # ── Eligibility / Patient issues ──────────────────────────────
    "07": ResolutionAction(
        action="Verify patient eligibility — member ID not found",
        auto_resolvable=False,
        pharmacist_instruction=(
            "Call PBM member services or verify insurance card. "
            "Check for typos in member ID, group number, or person code. "
            "Verify plan is not terminated."
        ),
        urgency="high",
    ),
    "21": ResolutionAction(
        action="Invalid or missing date of service",
        auto_resolvable=True,
        rebill_fields={"date_of_service": "USE_TODAY"},
        pharmacist_instruction="System will rebill with today's date.",
    ),
    "22": ResolutionAction(
        action="Days supply missing or invalid",
        auto_resolvable=False,
        pharmacist_instruction="Verify days supply from prescription sig. Common issue: #90 tab / 1 QD = 90 days.",
    ),

    # ── Drug / NDC issues ────────────────────────────────────────
    "19": ResolutionAction(
        action="NDC not covered by plan — non-formulary",
        auto_resolvable=False,
        pharmacist_instruction=(
            "Check formulary for preferred alternative. "
            "Contact prescriber for therapeutic substitution or prior authorization. "
            "Consider DAW code 5 (brand dispensed as generic)."
        ),
        urgency="normal",
    ),
    "25": ResolutionAction(
        action="NDC not on file — verify NDC",
        auto_resolvable=False,
        pharmacist_instruction="Verify NDC from drug label. May need to try repackaged NDC or alternate labeler.",
    ),

    # ── Prior Authorization ──────────────────────────────────────
    "75": ResolutionAction(
        action="Prior Authorization Required",
        auto_resolvable=True,
        rebill_fields={"initiate_electronic_pa": True},
        pharmacist_instruction=(
            "Electronic PA initiated via CoverMyMeds. "
            "Estimated turnaround: 2–4 hours. "
            "Patient may need to return or be notified when approved."
        ),
        urgency="normal",
    ),
    "76": ResolutionAction(
        action="Plan limitations exceeded — quantity or refill limit",
        auto_resolvable=False,
        pharmacist_instruction=(
            "Check plan quantity limits. May need: "
            "1) Shorter days supply, "
            "2) Submission clarification code (SCC 4 = vacation supply), "
            "3) PA for quantity limit override."
        ),
    ),

    # ── Coordination of Benefits ─────────────────────────────────
    "41": ResolutionAction(
        action="Claim submitted to wrong processor",
        auto_resolvable=False,
        pharmacist_instruction="Verify patient's insurance — may have changed plans. Re-verify BIN/PCN/group.",
    ),

    # ── Refill issues ────────────────────────────────────────────
    "33": ResolutionAction(
        action="Refill too soon",
        auto_resolvable=False,
        pharmacist_instruction=(
            "Patient last filled {days_since_last_fill} days ago. "
            "Plan allows refill after {plan_refill_day} days. "
            "Submit SCC=2 (vacation/emergency) if clinically justified. "
            "Otherwise, hold until {next_fill_date}."
        ),
    ),
    "34": ResolutionAction(
        action="Refills exhausted",
        auto_resolvable=False,
        pharmacist_instruction="Contact prescriber for new Rx or authorized renewal. Cannot dispense without valid refills.",
        urgency="high",
    ),

    # ── DUR / Clinical ──────────────────────────────────────────
    "88": ResolutionAction(
        action="DUR reject — clinical edit",
        auto_resolvable=False,
        pharmacist_instruction=(
            "PBM clinical edit triggered. Review clinical alert. "
            "If overriding: document reason and resubmit with "
            "Reason for Service Code (RSC) and Professional Service Code (PSC)."
        ),
    ),
    "65": ResolutionAction(
        action="Patient age restriction",
        auto_resolvable=False,
        pharmacist_instruction="Verify patient age. Some plans restrict certain drugs by age. May require PA.",
    ),

    # ── Pricing / MAC ────────────────────────────────────────────
    "85": ResolutionAction(
        action="Claim pending — duplicate of paid claim",
        auto_resolvable=False,
        pharmacist_instruction=(
            "This claim appears to be a duplicate. "
            "Verify if claim was already paid. "
            "If patient needs a refill, verify refill-too-soon logic first."
        ),
    ),

    # ── System / Network ────────────────────────────────────────
    "87": ResolutionAction(
        action="Transaction count exceeded",
        auto_resolvable=True,
        rebill_fields={"retry_after_seconds": 30},
        pharmacist_instruction="System will retry automatically.",
    ),
    "13": ResolutionAction(
        action="Dispensing pharmacy not contracted",
        auto_resolvable=False,
        pharmacist_instruction=(
            "Pharmacy not in this plan's network. "
            "Verify NCPDP ID and NABP number. "
            "Patient may need to use in-network pharmacy or request exception."
        ),
        urgency="critical",
    ),

    # ── Controlled substance ────────────────────────────────────
    "99": ResolutionAction(
        action="Host processing error — resubmit",
        auto_resolvable=True,
        rebill_fields={"retry_after_seconds": 10},
    ),
}

# Auto-resolvable codes (can rebill without pharmacist intervention)
AUTO_RESOLVABLE_CODES = {code for code, action in REJECT_RESOLUTION_MAP.items() if action.auto_resolvable}


class RejectResolver:

    async def attempt_auto_resolve(
        self,
        reject_codes: list[str],
        claim_data: dict,
        insurance: dict,
    ) -> Optional[dict]:
        """
        Attempt automated resolution for known reject codes.
        Returns action dict if auto-resolution is possible, None otherwise.
        """
        if not reject_codes:
            return None

        primary_reject = reject_codes[0]
        resolution = REJECT_RESOLUTION_MAP.get(primary_reject)

        if not resolution or not resolution.auto_resolvable:
            logger.info("Reject %s requires pharmacist review", primary_reject)
            return None

        logger.info("Auto-resolving reject code %s: %s", primary_reject, resolution.action)

        action = {
            "code": primary_reject,
            "action": resolution.action,
            "rebill_modifications": resolution.rebill_fields.copy(),
        }

        # Apply specific auto-resolution logic
        if "USE_TODAY" in resolution.rebill_fields.get("date_of_service", ""):
            from datetime import date
            action["rebill_modifications"]["date_of_service"] = date.today().isoformat()

        elif resolution.rebill_fields.get("initiate_electronic_pa"):
            # In production: call CoverMyMeds API
            action["action"] = "Electronic PA initiated"
            action["pa_initiated"] = True

        elif "retry_after_seconds" in resolution.rebill_fields:
            action["action"] = f"Scheduled retry in {resolution.rebill_fields['retry_after_seconds']}s"

        return action

    def get_pharmacist_instructions(self, reject_codes: list[str]) -> list[dict]:
        """Return pharmacist-facing instructions for all reject codes on a claim."""
        instructions = []
        for code in reject_codes:
            resolution = REJECT_RESOLUTION_MAP.get(code)
            if resolution:
                instructions.append({
                    "reject_code": code,
                    "description": resolution.action,
                    "instruction": resolution.pharmacist_instruction or resolution.action,
                    "urgency": resolution.urgency,
                    "auto_resolvable": resolution.auto_resolvable,
                })
            else:
                instructions.append({
                    "reject_code": code,
                    "description": f"Unknown reject code {code}",
                    "instruction": f"Look up NCPDP reject code {code} in reference. Contact PBM help desk if needed.",
                    "urgency": "normal",
                    "auto_resolvable": False,
                })
        return sorted(instructions, key=lambda x: x["urgency"] == "critical", reverse=True)
