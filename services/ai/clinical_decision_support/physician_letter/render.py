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
