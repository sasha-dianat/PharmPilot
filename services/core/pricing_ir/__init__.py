"""Iranian pharmacy pricing & insurance-adjudication engine.

Deterministic, config-driven calculation of consumer price, insurer share,
patient payable, price-differential (مابه‌التفاوت), technical fee (حق فنی) and
VAT for a prescription basket. All tariffs live in `config.py` (editable, no
logic change) because Iranian tariffs are revised yearly.
"""
