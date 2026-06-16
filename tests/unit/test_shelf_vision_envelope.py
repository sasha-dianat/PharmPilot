from services.ai.shelf_vision.verifier import shelf_verify


def test_degrades_gracefully_with_no_model():
    env = shelf_verify(image_base64=None, staged_ndc="111", staged_lot="L1",
                       staged_quantity=10, expected_drug_name="metformin", expected_drug_form="tablet")
    assert env["degraded"] is True
    assert env["tier_used"] == "local"
    r = env["result"]
    assert r["count_verdict"] in ("pass", "warn", "block")
    assert r["counted_items"] is None  # no model → cannot count
    assert "manual confirmation" in " ".join(r["advisory_notes"]).lower()


def test_envelope_shape():
    env = shelf_verify(image_base64=None, staged_ndc="111", staged_lot="L1",
                       staged_quantity=5, expected_drug_name="x", expected_drug_form="tablet")
    for k in ("result", "tier_used", "confidence", "degraded", "options_offline"):
        assert k in env
    for k in ("counted_items", "count_confidence", "count_delta", "count_verdict",
              "drug_name_ocr", "drug_name_match", "drug_form_detected", "drug_form_match",
              "bounding_boxes", "advisory_notes"):
        assert k in env["result"]
