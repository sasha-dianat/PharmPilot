"""
Unit tests for Phases 15-18: Specialty, Compounding, EPCS, Cache, Notifications.
"""
import pytest
from datetime import date, timedelta
from uuid import uuid4


# ── REMS Engine ────────────────────────────────────────────────────────────

class TestREMSEngine:

    def _make_engine(self):
        from services.core.specialty.rems_engine import REMSEngine, REMSProgram
        return REMSEngine(
            pharmacy_rems_ids={
                "iPLEDGE": "PHARM001",
                "CLOZAPINE_REMS": "PHARM002",
            }
        )

    def test_identify_isotretinoin_as_ipledge(self):
        from services.core.specialty.rems_engine import REMSEngine, REMSProgram
        engine = REMSEngine()
        program = engine.identify_rems_program("00004012345")  # Roche prefix
        assert program == REMSProgram.IPLEDGE

    def test_ipledge_blocks_without_pregnancy_test(self):
        from services.core.specialty.rems_engine import REMSEngine, REMSProgram, REMSAuthorizationRequest
        engine = self._make_engine()
        request = REMSAuthorizationRequest(
            rems_program=REMSProgram.IPLEDGE,
            patient_rems_id="PAT001",
            prescriber_rems_id="PRESC001",
            pregnancy_test_negative=None,  # No test date provided
            contraception_confirmed=True,
        )
        result = engine.validate_dispense_readiness(request)
        assert not result.authorized, "Missing pregnancy test must block iPLEDGE dispense"
        assert any("pregnancy" in r.lower() for r in result.denial_reasons)

    def test_ipledge_blocks_positive_pregnancy_test(self):
        from services.core.specialty.rems_engine import REMSEngine, REMSProgram, REMSAuthorizationRequest
        engine = self._make_engine()
        request = REMSAuthorizationRequest(
            rems_program=REMSProgram.IPLEDGE,
            patient_rems_id="PAT001",
            prescriber_rems_id="PRESC001",
            pregnancy_test_negative=False,  # POSITIVE
            pregnancy_test_date=date.today(),
            contraception_confirmed=True,
        )
        result = engine.validate_dispense_readiness(request)
        assert not result.authorized, "Positive pregnancy test = HARD STOP"
        assert any("positive" in r.lower() or "hard stop" in r.lower()
                   for r in result.denial_reasons)

    def test_ipledge_approves_compliant_request(self):
        from services.core.specialty.rems_engine import REMSEngine, REMSProgram, REMSAuthorizationRequest
        engine = self._make_engine()
        request = REMSAuthorizationRequest(
            rems_program=REMSProgram.IPLEDGE,
            patient_rems_id="PAT001",
            prescriber_rems_id="PRESC001",
            pregnancy_test_negative=True,
            pregnancy_test_date=date.today() - timedelta(days=3),  # Within 7 days
            contraception_confirmed=True,
        )
        result = engine.validate_dispense_readiness(request)
        assert result.authorized, f"Compliant iPLEDGE request denied: {result.denial_reasons}"
        assert result.authorization_number is not None

    def test_clozapine_blocks_low_anc(self):
        from services.core.specialty.rems_engine import REMSEngine, REMSProgram, REMSAuthorizationRequest
        engine = self._make_engine()
        request = REMSAuthorizationRequest(
            rems_program=REMSProgram.CLOZARIL,
            patient_rems_id="PAT002",
            anc_value=1200.0,          # Below 1500 threshold
            anc_test_date=date.today(),
        )
        result = engine.validate_dispense_readiness(request)
        assert not result.authorized, "ANC < 1500 must block clozapine dispense"
        assert any("1500" in r or "anc" in r.lower() for r in result.denial_reasons)

    def test_clozapine_approves_adequate_anc(self):
        from services.core.specialty.rems_engine import REMSEngine, REMSProgram, REMSAuthorizationRequest
        engine = self._make_engine()
        request = REMSAuthorizationRequest(
            rems_program=REMSProgram.CLOZARIL,
            patient_rems_id="PAT002",
            anc_value=2500.0,          # Normal
            anc_test_date=date.today() - timedelta(days=2),
        )
        result = engine.validate_dispense_readiness(request)
        assert result.authorized, f"Normal ANC should approve clozapine: {result.denial_reasons}"


# ── Compounding BUD Calculator ─────────────────────────────────────────────

class TestBUDCalculator:

    def test_non_sterile_room_temp_180_days(self):
        from services.core.compounding.formula_engine import BUDCalculator, CompoundingFormula, CompoundingCategory
        calc = BUDCalculator()
        formula = CompoundingFormula(category=CompoundingCategory.NON_STERILE, storage_condition='room_temp')
        bud_date, rationale = calc.calculate(formula, [])
        days = (bud_date - date.today()).days
        assert days == 180, f"Non-sterile RT should be 180 days, got {days}"

    def test_sterile_low_risk_12h(self):
        from services.core.compounding.formula_engine import BUDCalculator, CompoundingFormula, CompoundingCategory
        calc = BUDCalculator()
        formula = CompoundingFormula(category=CompoundingCategory.STERILE_LOW_RISK, storage_condition='room_temp')
        bud_date, rationale = calc.calculate(formula, [])
        # Cat 1 sterile RT = 12 hours = same day
        assert bud_date == date.today(), f"Cat 1 sterile RT should expire today, got {bud_date}"

    def test_bud_constrained_by_ingredient_expiry(self):
        from services.core.compounding.formula_engine import BUDCalculator, CompoundingFormula, CompoundingCategory
        calc = BUDCalculator()
        formula = CompoundingFormula(category=CompoundingCategory.NON_STERILE, storage_condition='room_temp')
        # Ingredient expiring in 30 days — should constrain the 180-day BUD
        early_expiry = date.today() + timedelta(days=30)
        bud_date, rationale = calc.calculate(formula, [early_expiry])
        assert bud_date == early_expiry, "BUD must be constrained by earliest ingredient expiry"
        assert "ingredient expiry" in rationale.lower() or "constrained" in rationale.lower()

    def test_formula_validation_requires_approval(self):
        from services.core.compounding.formula_engine import CompoundingFormula, CompoundingCategory
        formula = CompoundingFormula(
            formula_name="Test Cream",
            category=CompoundingCategory.NON_STERILE,
            instructions="Mix ingredients",
            ingredients=[{"ingredient_name": "Base", "quantity": 100, "unit": "g"}],
            # No approved_by_pharmacist_id
        )
        errors = formula.validate()
        assert any("pharmacist approval" in e.lower() for e in errors)


# ── EPCS Engine ────────────────────────────────────────────────────────────

class TestEPCSEngine:

    def test_totp_generate_verify_cycle(self):
        from services.core.epcs.epcs_engine import TOTPManager
        totp = TOTPManager()
        secret = totp.generate_secret()
        assert len(secret) == 32, "TOTP secret should be 32 base32 chars"
        code = totp.current_code(secret)
        assert totp.verify(secret, code), "Current TOTP code must verify"

    def test_totp_wrong_code_rejected(self):
        from services.core.epcs.epcs_engine import TOTPManager
        totp = TOTPManager()
        secret = totp.generate_secret()
        assert not totp.verify(secret, "000000"), "Wrong code must be rejected"
        assert not totp.verify(secret, ""),       "Empty code must be rejected"
        assert not totp.verify("", "123456"),     "Empty secret must fail"

    def test_dea_validator_used_in_epcs(self):
        from services.core.epcs.epcs_engine import EPCSPharmacyVerifier, TOTPManager, EPCSAuditLogger
        verifier = EPCSPharmacyVerifier(TOTPManager(), EPCSAuditLogger())
        valid, msg = verifier.verify_prescriber_dea_active("AB1234563")
        assert valid, f"Valid DEA should pass EPCS verification: {msg}"

        invalid, msg2 = verifier.verify_prescriber_dea_active("AB1234560")
        assert not invalid, "Invalid check digit must fail EPCS DEA verification"

    def test_audit_log_fields_complete(self):
        from services.core.epcs.epcs_engine import AUDIT_LOG_REQUIRED_FIELDS
        required = {
            "prescription_id", "prescriber_npi", "prescriber_dea",
            "drug_ndc", "dea_schedule", "patient_id", "pharmacy_ncpdp",
            "pharmacist_npi", "action", "timestamp_utc",
            "totp_verified", "biometric_verified", "event_hash",
        }
        for field in required:
            assert field in AUDIT_LOG_REQUIRED_FIELDS, \
                f"DEA-required field '{field}' missing from audit log spec"


# ── Redis Cache ────────────────────────────────────────────────────────────

class TestPharmacyCache:

    @pytest.mark.asyncio
    async def test_cache_miss_returns_none(self):
        from services.platform.cache import PharmacyCache
        cache = PharmacyCache(redis_client=None)
        result = await cache.get_bin_routing("999999")
        assert result is None, "Cache miss must return None"

    @pytest.mark.asyncio
    async def test_set_then_get_returns_value(self):
        from services.platform.cache import PharmacyCache
        cache = PharmacyCache(redis_client=None)
        routing = {"url": "https://test.com", "switch": "test"}
        await cache.set_bin_routing("004336", routing)
        result = await cache.get_bin_routing("004336")
        assert result == routing, "After set, get must return the same value"

    @pytest.mark.asyncio
    async def test_invalidate_removes_cached_value(self):
        from services.platform.cache import PharmacyCache
        cache = PharmacyCache(redis_client=None)
        await cache.set_bin_routing("004336", {"url": "https://test.com"})
        await cache.invalidate_bin_routing("004336")
        result = await cache.get_bin_routing("004336")
        assert result is None, "After invalidation, get must return None"

    @pytest.mark.asyncio
    async def test_patient_insurance_cache(self):
        from services.platform.cache import PharmacyCache
        cache = PharmacyCache(redis_client=None)
        insurance = [{"bin_number": "004336", "member_id": "TEST001", "priority": 1}]
        patient_id = str(uuid4())
        await cache.set_patient_insurance(patient_id, insurance)
        result = await cache.get_patient_insurance(patient_id)
        assert result == insurance

    @pytest.mark.asyncio
    async def test_ttl_constants_are_reasonable(self):
        from services.platform.cache import (
            TTL_BIN_PCN_ROUTING, TTL_DRUG_DATABASE,
            TTL_PATIENT_INSURANCE, TTL_RX_QUEUE_COUNTS,
        )
        # Patient insurance must expire faster than drug data (more volatile)
        assert TTL_PATIENT_INSURANCE < TTL_DRUG_DATABASE
        # Rx queue must be near real-time
        assert TTL_RX_QUEUE_COUNTS <= 30
        # BIN routing should be stable (at least 30 min)
        assert TTL_BIN_PCN_ROUTING >= 1800


# ── Prior Auth Engine ──────────────────────────────────────────────────────

class TestPriorAuthEngine:

    def test_denial_guidance_for_known_reason(self):
        from services.core.specialty.prior_auth_engine import ElectronicPAEngine
        engine = ElectronicPAEngine()
        guidance = engine.get_denial_guidance("step_therapy_required")
        assert len(guidance) > 20, "Denial guidance should be substantive"
        assert "step therapy" in guidance.lower() or "failure" in guidance.lower()

    def test_denial_guidance_for_unknown_reason(self):
        from services.core.specialty.prior_auth_engine import ElectronicPAEngine
        engine = ElectronicPAEngine()
        guidance = engine.get_denial_guidance("unknown_reason_xyz")
        assert len(guidance) > 10, "Should return generic guidance for unknown reasons"

    def test_pa_status_enum_complete(self):
        from services.core.specialty.prior_auth_engine import PAStatus
        required = {"initiated", "pending", "approved", "denied", "appealed", "cancelled"}
        actual = {s.value for s in PAStatus}
        missing = required - actual
        assert not missing, f"Missing PA status values: {missing}"

    def test_pa_urgency_options(self):
        from services.core.specialty.prior_auth_engine import PAUrgency
        assert PAUrgency.URGENT.value == "urgent"
        assert PAUrgency.NON_URGENT.value == "non_urgent"

    def test_340b_medicaid_carve_out(self):
        """Medicaid claims must never use 340B pricing (duplicate discount prohibition)."""
        from services.core.billing.split_billing_340b import Split340BEngine, CoveredEntityConfig
        import asyncio
        from uuid import uuid4
        config = CoveredEntityConfig(
            entity_id="CE001", entity_name="Test FQHC", entity_type="FQHC",
            contract_pharmacies=["1234567"],
        )
        engine = Split340BEngine(covered_entity_config=config)
        decision = asyncio.run(engine.evaluate_prescription(
            prescription_id=uuid4(), patient_id=uuid4(),
            ndc11="00071015423", insurance_plan_type="medicaid",
            pharmacy_ncpdp="1234567", wac_price=50.00,
        ))
        assert not decision.use_340b, "Medicaid must not use 340B (duplicate discount prohibition)"
        assert decision.medicaid_carve_out is True
