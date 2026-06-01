"""Renal dose adjustment module."""
RENAL_RESTRICTED_DRUGS = {
    "metformin": {"contraindicated_below_egfr": 30, "reduce_dose_below_egfr": 45},
    "gabapentin": {"contraindicated_below_egfr": None, "reduce_dose_below_egfr": 60},
    "nitrofurantoin": {"contraindicated_below_egfr": 30, "reduce_dose_below_egfr": None},
    "colchicine": {"contraindicated_below_egfr": 30, "reduce_dose_below_egfr": 50},
}

class RenalDosingModule:
    def evaluate(self, drug: dict, egfr: float) -> str | None:
        drug_name = drug.get("drug_name", "").lower()
        for restricted_name, thresholds in RENAL_RESTRICTED_DRUGS.items():
            if restricted_name in drug_name:
                ci_threshold = thresholds.get("contraindicated_below_egfr")
                dose_threshold = thresholds.get("reduce_dose_below_egfr")
                if ci_threshold and egfr < ci_threshold:
                    return f"CONTRAINDICATED: {drug_name} is contraindicated at eGFR < {ci_threshold} (current eGFR: {egfr:.1f})"
                if dose_threshold and egfr < dose_threshold:
                    return f"DOSE REDUCTION REQUIRED: {drug_name} requires dose adjustment at eGFR < {dose_threshold} (current eGFR: {egfr:.1f})"
        return None
