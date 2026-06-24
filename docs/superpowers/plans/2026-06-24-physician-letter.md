# Physician Responsibility Letter Implementation Plan (Phase 2b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a printable, PHI-safe physician responsibility letter when a prescription carries a Contraindicated interaction — LLM-composed with placeholder tokens, identifiers substituted locally, deterministic Persian/English fallback, persisted verbatim for legal record.

**Architecture:** A small `physician_letter` package: build de-identified clinical content → compose a placeholdered letter (LLM via `local_llm.generate`, validated; else a built-in template) → substitute real identifiers locally → render RTL HTML → persist verbatim + content hash. Two `cds.py` endpoints expose generate + reprint; a React modal previews and browser-prints it.

**Tech Stack:** Python 3.12, FastAPI, async SQLAlchemy, Alembic, PyYAML, pytest; React 19 + react-query. Run python via `/Users/sashad85/miniforge3/bin/python`. Alembic needs `DATABASE_URL=postgresql+asyncpg://pharmpilot:change_in_production@127.0.0.1:5433/pharmpilot`.

**Spec:** `docs/superpowers/specs/2026-06-24-physician-letter-design.md`

---

## File structure

```
services/ai/clinical_decision_support/physician_letter/
  __init__.py
  placeholders.py   # ALLOWED_TOKENS + substitute()
  content.py        # ClinicalContent + build_clinical_content()
  templates.py      # LETTER_VERSION + deterministic_template() (fa/en)
  validate.py       # is_safe_template()
  compose.py        # build_prompt(), compose_via_llm(), compose()
  render.py         # render_html()
shared/models/clinical.py                       # + PhysicianLetter model
data/migrations/versions/0015_physician_letters.py
services/platform/routers/cds.py                # POST/GET /cds/physician-letter
frontend/workstation/src/lib/api.ts             # generatePhysicianLetter, getPhysicianLetter
frontend/workstation/src/components/PhysicianLetterModal.tsx
frontend/workstation/src/components/InteractionReportPanel.tsx  # trigger button
tests/unit/test_physician_letter.py
tests/unit/test_physician_letter_endpoint.py
```

---

## Task 1: Placeholder tokens + substitution

**Files:**
- Create: `services/ai/clinical_decision_support/physician_letter/__init__.py` (empty)
- Create: `services/ai/clinical_decision_support/physician_letter/placeholders.py`
- Test: `tests/unit/test_physician_letter.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_physician_letter.py
import pytest
from services.ai.clinical_decision_support.physician_letter.placeholders import (
    ALLOWED_TOKENS, substitute,
)


def test_allowed_tokens_set():
    assert "{{PATIENT_NAME}}" in ALLOWED_TOKENS
    assert "{{COUNCIL_ID}}" in ALLOWED_TOKENS


def test_substitute_replaces_present_tokens():
    out = substitute("Dr {{PHYSICIAN_NAME}} / {{COUNCIL_ID}}",
                     {"{{PHYSICIAN_NAME}}": "Who", "{{COUNCIL_ID}}": "NP-7"})
    assert out == "Dr Who / NP-7"
    assert "{{" not in out


def test_substitute_raises_on_unfilled_token():
    with pytest.raises(ValueError):
        substitute("Hi {{PATIENT_NAME}}", {})   # token present, no value → leftover {{


def test_substitute_rejects_unknown_token():
    with pytest.raises(ValueError):
        substitute("{{NOT_A_TOKEN}}", {"{{NOT_A_TOKEN}}": "x"})
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_physician_letter.py -q`
Expected: FAIL — module missing

- [ ] **Step 3: Implement**

```python
# services/ai/clinical_decision_support/physician_letter/placeholders.py
from __future__ import annotations

import re

ALLOWED_TOKENS = {
    "{{PATIENT_NAME}}", "{{PATIENT_NATIONAL_ID}}", "{{PHYSICIAN_NAME}}",
    "{{COUNCIL_ID}}", "{{PHARMACIST_NAME}}", "{{PHARMACIST_LICENSE}}",
    "{{PHARMACY_NAME}}", "{{DATE}}",
}

_TOKEN_RE = re.compile(r"\{\{[A-Z_]+\}\}")


def substitute(template: str, values: dict[str, str]) -> str:
    """Replace identifier placeholders with real values. Raises ValueError on an
    unknown token or any leftover placeholder (never emit an unfilled letter)."""
    for tok in _TOKEN_RE.findall(template):
        if tok not in ALLOWED_TOKENS:
            raise ValueError(f"unknown placeholder {tok}")
    out = template
    for tok, val in values.items():
        if tok in ALLOWED_TOKENS:
            out = out.replace(tok, str(val))
    if _TOKEN_RE.search(out):
        raise ValueError(f"unfilled placeholder remains: {_TOKEN_RE.search(out).group()}")
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_physician_letter.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add services/ai/clinical_decision_support/physician_letter/__init__.py \
        services/ai/clinical_decision_support/physician_letter/placeholders.py \
        tests/unit/test_physician_letter.py
git commit -m "feat(cds): physician-letter placeholder tokens + safe substitution"
```

---

## Task 2: De-identified clinical content builder

**Files:**
- Create: `services/ai/clinical_decision_support/physician_letter/content.py`
- Test: `tests/unit/test_physician_letter.py`

- [ ] **Step 1: Write the failing test (append)**

```python
from services.ai.clinical_decision_support.physician_letter.content import (
    build_clinical_content, ClinicalContent,
)
from services.ai.intelligence_core.phi_scrub import scrub_identifiers


def test_build_content_is_deidentified():
    findings = [{
        "participants": [{"name": "warfarin"}, {"name": "phenelzine"}],
        "mechanism": "MAOI with serotonergic agent: hypertensive crisis.",
        "mechanism_basis": "serotonergic + MAOI",
        "severity": "Contraindicated",
    }]
    c = build_clinical_content(findings)
    assert isinstance(c, ClinicalContent)
    assert "warfarin" in c.drugs and "phenelzine" in c.drugs
    assert "hypertensive" in c.mechanism
    blob = f"{c.warning} {c.mechanism} {' '.join(c.drugs)}"
    _, hits = scrub_identifiers(blob)
    assert hits == []   # no identifiers in clinical content
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_physician_letter.py -q`
Expected: FAIL — `content` module missing

- [ ] **Step 3: Implement**

```python
# services/ai/clinical_decision_support/physician_letter/content.py
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ClinicalContent:
    warning: str
    mechanism: str
    drugs: list[str] = field(default_factory=list)


def build_clinical_content(findings: list[dict]) -> ClinicalContent:
    """De-identified clinical payload from contraindicated findings — the ONLY
    clinical text the LLM ever sees. Contains drug names + mechanism, no identifiers."""
    drugs: list[str] = []
    mechanisms: list[str] = []
    for f in findings:
        for p in f.get("participants", []):
            name = p.get("name")
            if name and name not in drugs:
                drugs.append(name)
        if f.get("mechanism"):
            mechanisms.append(f["mechanism"])
    warning = ("A contraindicated drug interaction was identified during pharmacist "
               "verification of this prescription.")
    return ClinicalContent(warning=warning, mechanism=" ".join(mechanisms), drugs=drugs)
```

- [ ] **Step 4: Run to verify pass**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_physician_letter.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/ai/clinical_decision_support/physician_letter/content.py tests/unit/test_physician_letter.py
git commit -m "feat(cds): de-identified clinical content builder for physician letter"
```

---

## Task 3: Deterministic templates (Persian + English)

**Files:**
- Create: `services/ai/clinical_decision_support/physician_letter/templates.py`
- Test: `tests/unit/test_physician_letter.py`

- [ ] **Step 1: Write the failing test (append)**

```python
from services.ai.clinical_decision_support.physician_letter.templates import (
    deterministic_template, LETTER_VERSION,
)
from services.ai.clinical_decision_support.physician_letter.content import ClinicalContent
from services.ai.clinical_decision_support.physician_letter.placeholders import substitute

_C = ClinicalContent(warning="A contraindicated interaction was found.",
                     mechanism="MAOI + serotonergic agent.", drugs=["warfarin", "phenelzine"])
_VALUES = {
    "{{PATIENT_NAME}}": "Ali Karimi", "{{PATIENT_NATIONAL_ID}}": "1234567890",
    "{{PHYSICIAN_NAME}}": "Dr Who", "{{COUNCIL_ID}}": "NP-77",
    "{{PHARMACIST_NAME}}": "Pat Pharm", "{{PHARMACIST_LICENSE}}": "LIC-9",
    "{{PHARMACY_NAME}}": "Central Pharmacy", "{{DATE}}": "2026-06-24",
}


def test_persian_template_inlines_content_and_substitutes():
    tpl = deterministic_template(_C, "fa")
    assert "warfarin" in tpl and "{{PATIENT_NAME}}" in tpl   # content inlined, identifiers as tokens
    letter = substitute(tpl, _VALUES)
    assert "Ali Karimi" in letter and "NP-77" in letter and "{{" not in letter


def test_english_template_available():
    assert "{{PHYSICIAN_NAME}}" in deterministic_template(_C, "en")


def test_unknown_language_falls_back_to_persian():
    assert deterministic_template(_C, "de") == deterministic_template(_C, "fa")


def test_letter_version_present():
    assert LETTER_VERSION
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_physician_letter.py -q`
Expected: FAIL — `templates` missing

- [ ] **Step 3: Implement**

```python
# services/ai/clinical_decision_support/physician_letter/templates.py
from __future__ import annotations

from .content import ClinicalContent

LETTER_VERSION = "letter-v1"

_FA = """بسمه تعالی

تاریخ: {{DATE}}
جناب آقای/سرکار خانم دکتر {{PHYSICIAN_NAME}} — شماره نظام پزشکی: {{COUNCIL_ID}}

با سلام و احترام،
در زمان بازبینی نسخهٔ بیمار {{PATIENT_NAME}} (کد ملی: {{PATIENT_NATIONAL_ID}}) یک تداخل داروییِ منع‌مصرف شناسایی شد.

هشدار: {warning}
سازوکار: {mechanism}
داروهای مرتبط: {drugs}

خواهشمند است در صورت صلاحدید و پذیرش مسئولیت بالینیِ تجویز این داروها، مراتب را تأیید فرمایید.

با احترام،
داروساز: {{PHARMACIST_NAME}} (شماره نظام: {{PHARMACIST_LICENSE}})
داروخانه: {{PHARMACY_NAME}}

— تأیید پزشک —
اینجانب تداخل فوق را بررسی نموده و مسئولیت بالینیِ تجویز این داروها را می‌پذیرم.
امضا: ____________     تاریخ: ____________     شماره نظام پزشکی: ____________
"""

_EN = """Date: {{DATE}}
Dr {{PHYSICIAN_NAME}} — Medical Council ID: {{COUNCIL_ID}}

Dear Doctor,
During pharmacist verification of the prescription for {{PATIENT_NAME}}
(National ID: {{PATIENT_NATIONAL_ID}}), a contraindicated drug interaction was identified.

Warning: {warning}
Mechanism: {mechanism}
Medications involved: {drugs}

If, in your clinical judgement, you choose to proceed and assume responsibility for
administering these medications, please confirm below.

Respectfully,
Pharmacist: {{PHARMACIST_NAME}} (License: {{PHARMACIST_LICENSE}})
Pharmacy: {{PHARMACY_NAME}}

— Physician acknowledgement —
I have reviewed the interaction above and assume clinical responsibility for administering
these medications.
Signature: ____________     Date: ____________     Medical Council ID: ____________
"""


def deterministic_template(content: ClinicalContent, language: str) -> str:
    body = _EN if language == "en" else _FA   # any non-en language → Persian
    return body.format(warning=content.warning, mechanism=content.mechanism,
                       drugs="، ".join(content.drugs) if body is _FA else ", ".join(content.drugs))
```

- [ ] **Step 4: Run to verify pass**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_physician_letter.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/ai/clinical_decision_support/physician_letter/templates.py tests/unit/test_physician_letter.py
git commit -m "feat(cds): deterministic Persian/English physician-letter templates"
```

---

## Task 4: Output validation

**Files:**
- Create: `services/ai/clinical_decision_support/physician_letter/validate.py`
- Test: `tests/unit/test_physician_letter.py`

- [ ] **Step 1: Write the failing test (append)**

```python
from services.ai.clinical_decision_support.physician_letter.validate import is_safe_template


def test_validate_accepts_clean_placeholder_template():
    assert is_safe_template("Dear {{PHYSICIAN_NAME}} ({{COUNCIL_ID}}), warning text.")


def test_validate_rejects_unknown_token():
    assert not is_safe_template("Dear {{DOCTOR}}")


def test_validate_rejects_stray_identifier():
    # model invented a national-id-like number instead of using the placeholder
    assert not is_safe_template("Patient national id 1234567890 has a problem.")


def test_validate_rejects_email_leak():
    assert not is_safe_template("contact dr@example.com")
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_physician_letter.py -q`
Expected: FAIL — `validate` missing

- [ ] **Step 3: Implement**

```python
# services/ai/clinical_decision_support/physician_letter/validate.py
from __future__ import annotations

import re

from services.ai.intelligence_core.phi_scrub import scrub_identifiers
from .placeholders import ALLOWED_TOKENS

_TOKEN_RE = re.compile(r"\{\{[A-Z_]+\}\}")


def is_safe_template(text: str) -> bool:
    """A composed template is safe only if every placeholder is allowed AND, once
    placeholders are stripped, no real identifier (national id / email / phone) remains —
    i.e. the model didn't invent or echo an identifier."""
    if not text:
        return False
    for tok in _TOKEN_RE.findall(text):
        if tok not in ALLOWED_TOKENS:
            return False
    stripped = _TOKEN_RE.sub(" ", text)
    _, hits = scrub_identifiers(stripped)
    return not hits
```

- [ ] **Step 4: Run to verify pass**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_physician_letter.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/ai/clinical_decision_support/physician_letter/validate.py tests/unit/test_physician_letter.py
git commit -m "feat(cds): physician-letter output validation (placeholder + identifier-leak guard)"
```

---

## Task 5: Compose orchestrator (LLM → validate → fallback)

**Files:**
- Create: `services/ai/clinical_decision_support/physician_letter/compose.py`
- Test: `tests/unit/test_physician_letter.py`

- [ ] **Step 1: Write the failing test (append)**

```python
from types import SimpleNamespace as NS
from services.ai.clinical_decision_support.physician_letter import compose as comp


def test_build_prompt_contains_no_identifiers():
    p = comp.build_prompt(_C, "fa")
    assert "warfarin" in p                      # de-identified clinical content present
    assert "Ali Karimi" not in p and "1234567890" not in p
    assert "{{PATIENT_NAME}}" in p              # instructs placeholder usage


def test_compose_uses_llm_when_safe(monkeypatch):
    safe = "Dear {{PHYSICIAN_NAME}} ({{COUNCIL_ID}}) re {{PATIENT_NAME}}. " + _C.mechanism
    async def _gen(*a, **k): return NS(text=safe, degraded=False, provider="groq", model="llama")
    monkeypatch.setattr(comp.local_llm, "generate", _gen)
    import asyncio
    text, source = asyncio.run(comp.compose(_C, "fa"))
    assert text == safe and source == "groq:llama"


def test_compose_falls_back_when_degraded(monkeypatch):
    async def _gen(*a, **k): return NS(text="", degraded=True, provider="none", model="")
    monkeypatch.setattr(comp.local_llm, "generate", _gen)
    import asyncio
    text, source = asyncio.run(comp.compose(_C, "fa"))
    assert source == "deterministic" and "{{PATIENT_NAME}}" in text


def test_compose_falls_back_when_llm_unsafe(monkeypatch):
    async def _gen(*a, **k):
        return NS(text="patient 1234567890 ...", degraded=False, provider="groq", model="x")
    monkeypatch.setattr(comp.local_llm, "generate", _gen)
    import asyncio
    text, source = asyncio.run(comp.compose(_C, "fa"))
    assert source == "deterministic"
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_physician_letter.py -q`
Expected: FAIL — `compose` missing

- [ ] **Step 3: Implement**

```python
# services/ai/clinical_decision_support/physician_letter/compose.py
from __future__ import annotations

import logging

from services.ai.intelligence_core import local_llm
from .content import ClinicalContent
from .templates import deterministic_template
from .validate import is_safe_template

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You write a formal, respectful clinical letter from a pharmacist to a physician. "
    "Use ONLY these placeholder tokens for any person, pharmacy, or date: "
    "{{PATIENT_NAME}}, {{PATIENT_NATIONAL_ID}}, {{PHYSICIAN_NAME}}, {{COUNCIL_ID}}, "
    "{{PHARMACIST_NAME}}, {{PHARMACIST_LICENSE}}, {{PHARMACY_NAME}}, {{DATE}}. "
    "NEVER invent or write a real name, ID number, email, or phone. End with a physician "
    "signature line."
)


def build_prompt(content: ClinicalContent, language: str) -> str:
    return (
        f"Language: {language}\n"
        f"Warning: {content.warning}\n"
        f"Mechanism: {content.mechanism}\n"
        f"Medications: {', '.join(content.drugs)}\n\n"
        "Write the letter now, in the requested language, using only the placeholder tokens "
        "for identifiers (e.g. address the physician as 'Dr {{PHYSICIAN_NAME}}')."
    )


async def compose_via_llm(content: ClinicalContent, language: str) -> tuple[str, str] | None:
    try:
        res = await local_llm.generate(
            build_prompt(content, language), system=_SYSTEM,
            max_tokens=900, temperature=0.2, phi=False, task="summarize")
    except Exception as exc:  # pragma: no cover
        logger.warning("[physician_letter] LLM compose failed: %s", exc)
        return None
    text = (getattr(res, "text", "") or "").strip()
    if getattr(res, "degraded", False) or not text or not is_safe_template(text):
        return None
    return text, f"{res.provider}:{res.model}"


async def compose(content: ClinicalContent, language: str) -> tuple[str, str]:
    out = await compose_via_llm(content, language)
    if out:
        return out
    return deterministic_template(content, language), "deterministic"
```

- [ ] **Step 4: Run to verify pass**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_physician_letter.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/ai/clinical_decision_support/physician_letter/compose.py tests/unit/test_physician_letter.py
git commit -m "feat(cds): physician-letter compose orchestrator (LLM→validate→deterministic)"
```

---

## Task 6: HTML render (RTL + print CSS)

**Files:**
- Create: `services/ai/clinical_decision_support/physician_letter/render.py`
- Test: `tests/unit/test_physician_letter.py`

- [ ] **Step 1: Write the failing test (append)**

```python
from services.ai.clinical_decision_support.physician_letter.render import render_html


def test_render_html_rtl_for_persian():
    html = render_html("متن نامه", "fa")
    assert 'dir="rtl"' in html and "@page" in html and "متن نامه" in html


def test_render_html_ltr_for_english():
    html = render_html("Letter body", "en")
    assert 'dir="ltr"' in html and "Letter body" in html
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_physician_letter.py -q`
Expected: FAIL — `render` missing

- [ ] **Step 3: Implement**

```python
# services/ai/clinical_decision_support/physician_letter/render.py
from __future__ import annotations

import html as _html


def render_html(letter_text: str, language: str) -> str:
    rtl = language != "en"
    direction = "rtl" if rtl else "ltr"
    body = _html.escape(letter_text)
    return f"""<!doctype html><html lang="{language}" dir="{direction}"><head>
<meta charset="utf-8">
<style>
@page {{ size: A4; margin: 2cm; }}
body {{ font-family: 'Vazirmatn','Tahoma',sans-serif; line-height: 1.9; color: #111;
        direction: {direction}; white-space: pre-wrap; font-size: 13pt; }}
@media print {{ .no-print {{ display: none; }} }}
</style></head>
<body dir="{direction}">{body}</body></html>"""
```

- [ ] **Step 4: Run to verify pass + the whole package suite**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_physician_letter.py -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add services/ai/clinical_decision_support/physician_letter/render.py tests/unit/test_physician_letter.py
git commit -m "feat(cds): physician-letter RTL/print HTML renderer"
```

---

## Task 7: PhysicianLetter model + migration

**Files:**
- Modify: `shared/models/clinical.py`
- Create: `data/migrations/versions/0015_physician_letters.py`

- [ ] **Step 1: Add the model** to `shared/models/clinical.py` (after `InteractionReportCache`)

```python
class PhysicianLetter(AuditedBase):
    """Verbatim legal record of a physician responsibility letter issued for a
    contraindicated interaction (identifiers substituted; content-hashed)."""
    __tablename__ = "physician_letters"

    patient_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    rx_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    pharmacy_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    prescriber_name: Mapped[str] = mapped_column(String(200), nullable=False)
    prescriber_council_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    pharmacist_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    pharmacist_name: Mapped[str] = mapped_column(String(200), nullable=False)
    pharmacist_license: Mapped[str | None] = mapped_column(String(50), nullable=True)
    language: Mapped[str] = mapped_column(String(8), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    letter_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
```

(`Text`, `String`, `PG_UUID`, `UUID`, `Mapped`, `mapped_column` are already imported in
`clinical.py`.)

- [ ] **Step 2: Verify import**

Run: `/Users/sashad85/miniforge3/bin/python -c "from shared.models.clinical import PhysicianLetter; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Create migration**

```python
# data/migrations/versions/0015_physician_letters.py
"""physician responsibility letters

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "physician_letters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rx_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("prescriber_name", sa.String(200), nullable=False),
        sa.Column("prescriber_council_id", sa.String(40), nullable=True),
        sa.Column("pharmacist_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pharmacist_name", sa.String(200), nullable=False),
        sa.Column("pharmacist_license", sa.String(50), nullable=True),
        sa.Column("language", sa.String(8), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("model_version", sa.String(40), nullable=False),
        sa.Column("letter_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_physician_letters_patient_id", "physician_letters", ["patient_id"])
    op.create_index("ix_physician_letters_rx_id", "physician_letters", ["rx_id"])
    op.create_index("ix_physician_letters_pharmacy_id", "physician_letters", ["pharmacy_id"])


def downgrade() -> None:
    op.drop_table("physician_letters")
```

- [ ] **Step 4: Apply migration**

Run: `cd /Users/sashad85/PharmPilot-Claude && DATABASE_URL="postgresql+asyncpg://pharmpilot:change_in_production@127.0.0.1:5433/pharmpilot" /Users/sashad85/miniforge3/bin/python -m alembic -c alembic.ini upgrade head 2>&1 | tail -3`
Expected: `Running upgrade 0014 -> 0015`

- [ ] **Step 5: Commit**

```bash
git add shared/models/clinical.py data/migrations/versions/0015_physician_letters.py
git commit -m "feat(cds): physician_letters table + model"
```

---

## Task 8: Endpoints (generate + reprint)

**Files:**
- Modify: `services/platform/routers/cds.py`
- Test: `tests/unit/test_physician_letter_endpoint.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_physician_letter_endpoint.py
import asyncio
from types import SimpleNamespace as NS
from uuid import uuid4

from services.platform.routers import cds


class _Scalar:
    def __init__(self, rows): self._rows = rows
    def scalar_one_or_none(self): return self._rows[0] if self._rows else None


class _DB:
    """Returns patient, then pharmacy, for the two lookups; records the added letter."""
    def __init__(self, patient, pharmacy):
        self._seq = [_Scalar([patient]), _Scalar([pharmacy])]; self.added = []
    async def execute(self, *_a, **_k): return self._seq.pop(0) if self._seq else _Scalar([])
    def add(self, r): self.added.append(r)
    async def flush(self): pass


def test_generate_physician_letter_persists_and_substitutes(monkeypatch):
    # Force the deterministic path (no network) by making the LLM report degraded.
    from services.ai.clinical_decision_support.physician_letter import compose as comp
    async def _gen(*a, **k): return NS(text="", degraded=True, provider="none", model="")
    monkeypatch.setattr(comp.local_llm, "generate", _gen)

    ph = uuid4()
    patient = NS(id=uuid4(), pharmacy_id=ph, first_name="Ali", last_name="Karimi",
                 national_id="1234567890", is_deleted=False)
    pharmacy = NS(id=ph, name="Central Pharmacy")
    db = _DB(patient, pharmacy)
    staff = NS(id=uuid4(), pharmacy_id=ph, first_name="Pat", last_name="Pharm",
               pharmacist_license_number="LIC-9")
    body = cds.PhysicianLetterRequest(
        patient_id=patient.id, rx_id=None, language="fa",
        physician_name="Dr Who", council_id="NP-77",
        findings=[{"participants": [{"name": "warfarin"}, {"name": "phenelzine"}],
                   "mechanism": "MAOI + serotonergic.", "severity": "Contraindicated"}])
    out = asyncio.run(cds.generate_physician_letter(body, staff=staff, db=db))
    assert out["language"] == "fa" and out["id"]
    letter = out["letter_text"]
    assert "Ali Karimi" in letter and "NP-77" in letter and "{{" not in letter
    # a letter row + an audit row were added
    assert any(getattr(r, "letter_text", None) for r in db.added)
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_physician_letter_endpoint.py -q`
Expected: FAIL — `PhysicianLetterRequest` / `generate_physician_letter` missing

- [ ] **Step 3: Add to `cds.py`** (after the `acknowledge_interactions` handler)

```python
import hashlib

from services.ai.clinical_decision_support.physician_letter.compose import compose
from services.ai.clinical_decision_support.physician_letter.content import build_clinical_content
from services.ai.clinical_decision_support.physician_letter.placeholders import substitute
from services.ai.clinical_decision_support.physician_letter.render import render_html
from services.ai.clinical_decision_support.physician_letter.templates import LETTER_VERSION
from shared.models.clinical import PhysicianLetter
from shared.models.pharmacy import Pharmacy


class PhysicianLetterRequest(BaseModel):
    patient_id: UUID
    rx_id: UUID | None = None
    language: str = "fa"
    physician_name: str
    council_id: str | None = None
    findings: list[dict]


@router.post("/physician-letter")
async def generate_physician_letter(
    body: PhysicianLetterRequest,
    staff: Staff = Depends(require_permission("clinical:read")),
    db: AsyncSession = Depends(get_db),
):
    patient = (await db.execute(select(Patient).where(
        Patient.id == body.patient_id, Patient.pharmacy_id == staff.pharmacy_id,
        Patient.is_deleted == False))).scalar_one_or_none()  # noqa: E712
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    pharmacy = (await db.execute(select(Pharmacy).where(
        Pharmacy.id == staff.pharmacy_id))).scalar_one_or_none()

    content = build_clinical_content(body.findings)
    template_text, source = await compose(content, body.language)
    values = {
        "{{PATIENT_NAME}}": f"{patient.first_name} {patient.last_name}",
        "{{PATIENT_NATIONAL_ID}}": patient.national_id or "—",
        "{{PHYSICIAN_NAME}}": body.physician_name,
        "{{COUNCIL_ID}}": body.council_id or "—",
        "{{PHARMACIST_NAME}}": f"{getattr(staff,'first_name','')} {getattr(staff,'last_name','')}".strip(),
        "{{PHARMACIST_LICENSE}}": getattr(staff, "pharmacist_license_number", None) or "—",
        "{{PHARMACY_NAME}}": getattr(pharmacy, "name", None) or "—",
        "{{DATE}}": datetime.now(timezone.utc).date().isoformat(),
    }
    letter_text = substitute(template_text, values)
    content_hash = hashlib.sha256(letter_text.encode()).hexdigest()

    letter = PhysicianLetter(
        patient_id=patient.id, rx_id=body.rx_id, pharmacy_id=staff.pharmacy_id,
        prescriber_name=body.physician_name, prescriber_council_id=body.council_id,
        pharmacist_id=staff.id, pharmacist_name=values["{{PHARMACIST_NAME}}"],
        pharmacist_license=getattr(staff, "pharmacist_license_number", None),
        language=body.language, source=source, model_version=LETTER_VERSION,
        letter_text=letter_text, content_hash=content_hash,
        created_by=staff.id, updated_by=staff.id)
    db.add(letter)
    db.add(ClinicalAuditLog(
        user_id=staff.id, patient_id=patient.id, module="physician_letter",
        input_snapshot=_jsonable({"findings": body.findings, "language": body.language,
                                  "physician": {"name": body.physician_name, "council_id": body.council_id}}),
        output_snapshot=_jsonable({"source": source, "content_hash": content_hash}),
        rules_triggered=[f.get("rule_id") for f in body.findings],
        model_version=LETTER_VERSION, created_by=staff.id, updated_by=staff.id))
    await db.flush()
    return {"id": str(letter.id), "letter_text": letter_text,
            "letter_html": render_html(letter_text, body.language),
            "language": body.language, "source": source, "content_hash": content_hash}


@router.get("/physician-letter/{letter_id}")
async def get_physician_letter(
    letter_id: UUID,
    staff: Staff = Depends(require_permission("clinical:read")),
    db: AsyncSession = Depends(get_db),
):
    letter = (await db.execute(select(PhysicianLetter).where(
        PhysicianLetter.id == letter_id,
        PhysicianLetter.pharmacy_id == staff.pharmacy_id))).scalar_one_or_none()
    if not letter:
        raise HTTPException(status_code=404, detail="Letter not found")
    return {"id": str(letter.id), "letter_text": letter.letter_text,
            "letter_html": render_html(letter.letter_text, letter.language),
            "language": letter.language, "source": letter.source,
            "content_hash": letter.content_hash}
```

- [ ] **Step 4: Run to verify pass + routes register**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_physician_letter_endpoint.py -q && /Users/sashad85/miniforge3/bin/python -c "import services.platform.main; import services.platform.routers.cds as c; print([r.path for r in c.router.routes if 'physician' in r.path])"`
Expected: PASS + `['/physician-letter', '/physician-letter/{letter_id}']`

- [ ] **Step 5: Commit**

```bash
git add services/platform/routers/cds.py tests/unit/test_physician_letter_endpoint.py
git commit -m "feat(cds): physician-letter generate + reprint endpoints"
```

---

## Task 9: Frontend — API + modal + trigger

**Files:**
- Modify: `frontend/workstation/src/lib/api.ts`
- Create: `frontend/workstation/src/components/PhysicianLetterModal.tsx`
- Modify: `frontend/workstation/src/components/InteractionReportPanel.tsx`

- [ ] **Step 1: Add API methods** to `clinicalApi` in `api.ts` (after `acknowledgeInteractions`)

```ts
  generatePhysicianLetter: (body: {
    patient_id: string; rx_id?: string; language: string;
    physician_name: string; council_id?: string;
    findings: { rule_id?: string; participants: { name: string }[]; mechanism: string; severity: string }[];
  }) => apiClient.post('/cds/physician-letter', body),
```

- [ ] **Step 2: Create the modal**

```tsx
// frontend/workstation/src/components/PhysicianLetterModal.tsx
import { useState } from 'react'
import { clinicalApi } from '../lib/api'

interface Finding { rule_id?: string; participants: { name: string }[]; mechanism: string; severity: string }

export default function PhysicianLetterModal({
  patientId, rxId, findings, onClose,
}: { patientId: string; rxId?: string; findings: Finding[]; onClose: () => void }) {
  const [language, setLanguage] = useState('fa')
  const [physician, setPhysician] = useState('')
  const [councilId, setCouncilId] = useState('')
  const [letter, setLetter] = useState<string | null>(null)
  const [letterHtml, setLetterHtml] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const generate = async () => {
    if (!physician.trim() || !councilId.trim()) return
    setLoading(true)
    try {
      const { data } = await clinicalApi.generatePhysicianLetter({
        patient_id: patientId, rx_id: rxId, language,
        physician_name: physician, council_id: councilId, findings,
      })
      setLetter(data.letter_text)
      setLetterHtml(data.letter_html)
    } catch { setLetter('Could not generate letter — try again.'); setLetterHtml(null) }
    finally { setLoading(false) }
  }

  const print = () => {
    if (!letterHtml) return
    const w = window.open('', '_blank')
    if (!w) return
    w.document.write(letterHtml)   // server-rendered RTL/A4 HTML (single source of truth)
    w.document.close(); w.focus(); w.print()
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={onClose}>
      <div className="cd-card bg-surface max-w-2xl w-full p-4 space-y-3" onClick={e => e.stopPropagation()}>
        <div className="flex items-center">
          <h3 className="cd-ui text-sm font-bold text-ink">Physician responsibility letter</h3>
          <button onClick={onClose} className="ml-auto text-ink3 text-sm">✕</button>
        </div>
        <div className="flex gap-2">
          <select value={language} onChange={e => setLanguage(e.target.value)}
            className="cd-ui text-sm bg-surface2 border border-line2 rounded-lg px-2 py-2 text-ink">
            <option value="fa">فارسی</option><option value="en">English</option>
          </select>
          <input value={physician} onChange={e => setPhysician(e.target.value)} placeholder="Physician name"
            className="cd-ui flex-1 text-sm bg-surface2 border border-line2 rounded-lg px-3 py-2 text-ink" />
          <input value={councilId} onChange={e => setCouncilId(e.target.value)} placeholder="Council ID"
            className="cd-ui w-32 text-sm bg-surface2 border border-line2 rounded-lg px-3 py-2 text-ink" />
        </div>
        <div className="flex gap-2">
          <button onClick={generate} disabled={loading || !physician.trim() || !councilId.trim()}
            className="cd-ui px-4 py-2 bg-intel text-white text-sm rounded-lg disabled:opacity-50">
            {loading ? '…' : 'Generate'}</button>
          {letter && <button onClick={print} className="cd-ui px-4 py-2 bg-[#34d399] text-white text-sm rounded-lg">Print</button>}
        </div>
        {letter && (
          <div dir={language === 'en' ? 'ltr' : 'rtl'}
            className="cd-narr text-sm text-ink bg-surface2 border border-line rounded-lg p-3 max-h-80 overflow-y-auto whitespace-pre-wrap leading-[1.9]">
            {letter}
          </div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Trigger in `InteractionReportPanel.tsx`** — add the button when a Contraindicated
finding exists. Add the import and a modal-open state at the top of the component:

```tsx
import { useState } from 'react'
import PhysicianLetterModal from './PhysicianLetterModal'
```

Inside the component (with the other hooks):

```tsx
  const [letterOpen, setLetterOpen] = useState(false)
  const contraindicated = findings.filter(f => f.severity === 'Contraindicated')
```

Just before the closing `</div>` of the card's outer wrapper (after the findings list block),
add:

```tsx
          {contraindicated.length > 0 && (
            <button onClick={() => setLetterOpen(true)}
              className="cd-ui mt-1 w-full px-3 py-2 bg-[#ef4444] text-white text-sm rounded-lg hover:brightness-110">
              Generate physician letter
            </button>
          )}
          {letterOpen && patientId && (
            <PhysicianLetterModal
              patientId={patientId}
              findings={contraindicated.map(f => ({ rule_id: f.rule_id, participants: f.participants,
                mechanism: f.mechanism, severity: f.severity }))}
              onClose={() => setLetterOpen(false)} />
          )}
```

- [ ] **Step 4: Type-check**

Run: `cd frontend/workstation && npx tsc --noEmit 2>&1 | grep -E "PhysicianLetterModal|InteractionReportPanel|api.ts" | head; echo "exit ${PIPESTATUS[0]}"`
Expected: no errors (exit 0)

- [ ] **Step 5: Commit**

```bash
git add frontend/workstation/src/lib/api.ts \
        frontend/workstation/src/components/PhysicianLetterModal.tsx \
        frontend/workstation/src/components/InteractionReportPanel.tsx
git commit -m "feat(web): physician letter modal + contraindicated trigger"
```

---

## Task 10: End-to-end verification

- [ ] **Step 1: Full backend suite**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_physician_letter*.py tests/unit/test_interaction_*.py -q`
Expected: all pass

- [ ] **Step 2: PHI invariant — prompt never contains identifiers** (already covered by
`test_build_prompt_contains_no_identifiers`; re-run explicitly)

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_physician_letter.py -k "no_identifiers or unsafe or degraded" -q`
Expected: PASS

- [ ] **Step 3: Full frontend type-check**

Run: `cd frontend/workstation && npx tsc --noEmit; echo "exit $?"`
Expected: exit 0

- [ ] **Step 4: Browser smoke** (preview). With a patient that has a Contraindicated finding,
confirm the "Generate physician letter" button opens the modal, generating produces a letter
(Persian) with the substituted patient/physician names, and Print opens a clean RTL page.
Capture a screenshot. (If no seeded patient has a contraindication, verify the modal + generate
flow against any patient by passing a synthetic contraindicated finding through the panel — the
endpoint composes from the posted findings.)

- [ ] **Step 5: Refresh graphify + final commit**

```bash
graphify update . >/dev/null 2>&1 &
git add -A && git commit -m "chore(cds): finalize physician letter (phase 2b)" || echo "nothing to commit"
```

---

## Notes for the implementer

- **Identifiers never reach the model.** `compose_via_llm` sends only `build_prompt(content,…)`
  (de-identified) + the placeholder system prompt. Substitution happens *after*, in the endpoint.
  The `test_build_prompt_contains_no_identifiers` test is the guardrail — keep it green.
- **Always producible.** LLM down or unsafe → deterministic template. Never raise to the user.
- **Verbatim legal record.** Persist `letter_text` + `content_hash`; never regenerate-on-read.
- **Out of scope:** admin trail (Phase 2c), closed-loop physician sign-back, server-side PDF.
- The Task 2 test has an illustrative bad import line flagged for removal — delete it as noted.
