"""
Unit tests for PubMedIngestRequest validation (services/platform/routers/knowledge.py).

Regression: the model declared `query: str` (required) while the docstring/
comment said `search_query` was an accepted alternate name. A request body
containing only `search_query` therefore failed Pydantic validation with a
422 "field required" error before the handler's own
`q = body.search_query or body.query` fallback ever ran. `query` is now
Optional[str] so either field name validates; the handler still 422s if BOTH
are missing.
"""
from services.platform.routers.knowledge import PubMedIngestRequest


def test_accepts_query_only():
    body = PubMedIngestRequest(query="metformin renal dosing")
    assert body.query == "metformin renal dosing"
    assert body.search_query is None


def test_accepts_search_query_only():
    """This is the exact payload shape that used to 422 before the field was
    made Optional — a client sending only the legacy `search_query` name."""
    body = PubMedIngestRequest(search_query="warfarin interactions")
    assert body.search_query == "warfarin interactions"
    assert body.query is None


def test_accepts_both_fields():
    body = PubMedIngestRequest(query="a", search_query="b")
    assert body.query == "a"
    assert body.search_query == "b"


def test_allows_neither_field_at_model_level():
    """Pydantic itself must not reject an empty body — the handler is
    responsible for the 422 'must provide query or search_query' check,
    so both fields stay Optional at the model level."""
    body = PubMedIngestRequest()
    assert body.query is None
    assert body.search_query is None


def test_resolution_prefers_search_query_over_query():
    """Mirrors `q = body.search_query or body.query` in ingest_pubmed —
    when both are present, search_query (legacy CLI name) wins."""
    body = PubMedIngestRequest(query="a", search_query="b")
    assert (body.search_query or body.query) == "b"


def test_resolution_falls_back_to_query():
    body = PubMedIngestRequest(query="a")
    assert (body.search_query or body.query) == "a"


def test_resolution_is_none_when_neither_provided():
    body = PubMedIngestRequest()
    assert (body.search_query or body.query) is None
