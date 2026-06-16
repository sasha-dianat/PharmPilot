import json

import services.ai.provider_registry.registry as reg
from services.ai.provider_registry.registry import AIProviderRegistry, AITask, PHISensitivity


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(reg, "_AI_SETTINGS_PATH", tmp_path / "ai_settings.json")
    return AIProviderRegistry()


def test_set_preferred_provider_persists_and_reports(monkeypatch, tmp_path):
    r = _fresh(monkeypatch, tmp_path)
    r.set_preferred_provider("google")
    cfg = r.get_public_config()
    assert cfg["preferred_provider"] == "google"
    g = next(p for p in cfg["providers"] if p["provider_id"] == "google")
    assert g["is_preferred"] is True
    # persisted to disk
    saved = json.loads((tmp_path / "ai_settings.json").read_text())
    assert saved["preferred_provider"] == "google"


def test_unknown_provider_rejected(monkeypatch, tmp_path):
    r = _fresh(monkeypatch, tmp_path)
    try:
        r.set_preferred_provider("not-a-provider")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_set_api_key_activates_and_never_leaks(monkeypatch, tmp_path):
    r = _fresh(monkeypatch, tmp_path)
    r.set_api_key("google", "SECRET-GEMINI-KEY")
    cfg = r.get_public_config()
    g = next(p for p in cfg["providers"] if p["provider_id"] == "google")
    assert g["has_api_key"] is True
    # the raw key must never appear in the public config
    assert "SECRET-GEMINI-KEY" not in json.dumps(cfg)


def test_preferred_gemini_goes_first_even_for_phi(monkeypatch, tmp_path):
    r = _fresh(monkeypatch, tmp_path)
    r.set_api_key("google", "SECRET-GEMINI-KEY")   # makes google active (BAA)
    r.set_preferred_provider("google")
    chain = r.get_provider_chain(AITask.CLINICAL_CONSULTATION, PHISensitivity.HIGH)
    assert chain[0] == "google"  # Gemini first, and it's BAA so PHI-allowed
