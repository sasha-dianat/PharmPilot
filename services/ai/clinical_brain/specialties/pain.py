"""Pain management and controlled substance safety module."""
OPIOID_MME_FACTORS = {
    "codeine": 0.15, "tramadol": 0.1, "morphine": 1.0, "oxycodone": 1.5,
    "hydrocodone": 1.0, "hydromorphone": 4.0, "fentanyl_mcg": 0.13,
}
HIGH_DOSE_THRESHOLD_MME = 90.0

class PainManagementModule:
    def evaluate(self, new_prescription: dict, active_medications: list[dict], patient_age: int) -> str | None:
        drug_name = new_prescription.get("drug_name", "").lower()
        quantity = float(new_prescription.get("quantity_prescribed", 0))
        days_supply = int(new_prescription.get("days_supply", 1))

        for opioid_name, mme_factor in OPIOID_MME_FACTORS.items():
            if opioid_name in drug_name:
                strength_mg = float(new_prescription.get("strength_mg", 0))
                if strength_mg and days_supply:
                    daily_units = quantity / days_supply
                    daily_mme = daily_units * strength_mg * mme_factor
                    if daily_mme >= HIGH_DOSE_THRESHOLD_MME:
                        return (
                            f"HIGH-DOSE OPIOID: {drug_name} — calculated daily MME = {daily_mme:.0f} mg/day "
                            f"(CDC threshold: 90 MME/day). Consider naloxone co-prescribing. "
                            f"Verify PDMP before dispensing."
                        )
        return None
