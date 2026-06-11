from __future__ import annotations

from services.ai.pharmacogenomics import engine
from services.ai.pharmacogenomics.schema import PGxContext, PGxGenotype


def _first(context: PGxContext):
    result = engine.interpret_with_missing(context)
    assert result.interpretations
    return result.interpretations[0]


def test_cyp2c19_poor_clopidogrel_actionable():
    interpretation = _first(PGxContext(
        genotypes=[PGxGenotype(gene="CYP2C19", phenotype="poor metabolizer")],
        requested_drugs=["clopidogrel"],
    ))

    assert interpretation.actionable is True
    assert "reduced" in interpretation.clinical_implication.lower()
    assert "antiplatelet" in interpretation.clinical_implication.lower()
    assert "alternative antiplatelet" in interpretation.suggested_pharmacist_action.lower()


def test_cyp2d6_ultrarapid_codeine_toxicity_actionable():
    interpretation = _first(PGxContext(
        genotypes=[PGxGenotype(gene="CYP2D6", phenotype="ultrarapid metabolizer")],
        requested_drugs=["codeine"],
    ))

    text = f"{interpretation.clinical_implication} {interpretation.suggested_pharmacist_action}".lower()
    assert interpretation.actionable is True
    assert "toxicity" in text
    assert "avoid" in text


def test_cyp2d6_poor_tramadol_reduced_analgesia_actionable():
    interpretation = _first(PGxContext(
        genotypes=[PGxGenotype(gene="CYP2D6", phenotype="poor metabolizer")],
        requested_drugs=["tramadol"],
    ))

    text = f"{interpretation.clinical_implication} {interpretation.suggested_pharmacist_action}".lower()
    assert interpretation.actionable is True
    assert "reduced" in text
    assert "analgesia" in text


def test_cyp2c9_and_vkorc1_warfarin_sensitivity_actionable():
    result = engine.interpret_with_missing(PGxContext(
        genotypes=[
            PGxGenotype(gene="CYP2C9", phenotype="poor metabolizer"),
            PGxGenotype(gene="VKORC1", phenotype="high sensitivity"),
        ],
        requested_drugs=["warfarin"],
    ))

    assert len(result.interpretations) == 2
    combined = " ".join(
        f"{item.clinical_implication} {item.suggested_pharmacist_action}"
        for item in result.interpretations
    ).lower()
    assert all(item.actionable for item in result.interpretations)
    assert "bleeding risk" in combined
    assert "lower starting dose" in combined
    assert "inr" in combined


def test_slco1b1_poor_simvastatin_myopathy_actionable():
    interpretation = _first(PGxContext(
        genotypes=[PGxGenotype(gene="SLCO1B1", phenotype="poor function")],
        requested_drugs=["simvastatin"],
    ))

    text = f"{interpretation.clinical_implication} {interpretation.suggested_pharmacist_action}".lower()
    assert interpretation.actionable is True
    assert "myopathy" in text
    assert "lower" in text


def test_tpmt_poor_azathioprine_myelosuppression_actionable():
    interpretation = _first(PGxContext(
        genotypes=[PGxGenotype(gene="TPMT", phenotype="poor metabolizer")],
        requested_drugs=["azathioprine"],
    ))

    text = f"{interpretation.clinical_implication} {interpretation.suggested_pharmacist_action}".lower()
    assert interpretation.actionable is True
    assert interpretation.drug == "azathioprine"
    assert "severe" in text
    assert "myelosuppression" in text
    assert "alternative" in text or "drastically reduced" in text


def test_hla_b_1502_positive_carbamazepine_sjs_ten_actionable():
    interpretation = _first(PGxContext(
        genotypes=[PGxGenotype(gene="HLA-B*15:02", phenotype="positive")],
        requested_drugs=["carbamazepine"],
    ))

    text = f"{interpretation.clinical_implication} {interpretation.suggested_pharmacist_action}".lower()
    assert interpretation.actionable is True
    assert "sjs" in text or "stevens-johnson" in text
    assert "ten" in text or "toxic epidermal necrolysis" in text
    assert "avoid" in text


def test_diplotype_to_phenotype_resolution():
    interpretation = _first(PGxContext(
        genotypes=[PGxGenotype(gene="CYP2C19", diplotype="*2/*2")],
        requested_drugs=["clopidogrel"],
    ))

    assert interpretation.phenotype == "poor metabolizer"
    assert interpretation.actionable is True


def test_normal_metabolizer_returns_info_not_actionable_warning():
    result = engine.interpret_with_missing(PGxContext(
        genotypes=[PGxGenotype(gene="CYP2C19", phenotype="normal metabolizer")],
        requested_drugs=["clopidogrel"],
    ))

    assert len(result.interpretations) == 1
    interpretation = result.interpretations[0]
    assert interpretation.actionable is False
    assert "no pgx-based change" in interpretation.suggested_pharmacist_action.lower()
    assert "reduced clopidogrel activation" not in interpretation.clinical_implication.lower()


def test_no_rule_no_fabrication_and_unresolved_phenotype_missing():
    result = engine.interpret_with_missing(PGxContext(
        genotypes=[
            PGxGenotype(gene="CYP2C19", phenotype="poor metabolizer"),
            PGxGenotype(gene="SLCO1B1", diplotype="*99/*99"),
        ],
        requested_drugs=["warfarin", "simvastatin"],
    ))

    assert result.interpretations == []
    assert result.missing_information == ["phenotype for SLCO1B1 *99/*99 could not be resolved"]
