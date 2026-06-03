"""
Jalali (Persian / Shamsi / شمسی) calendar conversion.
=====================================================
Pure-Python, zero-dependency conversion between the Gregorian and Jalali
(Solar Hijri) calendars. Used for the dual-store date strategy:

  - date_of_birth          (Gregorian, canonical — date math, indexing, DUR age calc)
  - date_of_birth_jalali   (Jalali string "YYYY/MM/DD" — display + source fidelity)

Algorithm: the classic Kazimierz M. Borkowski / Behrang Noruzi Niya
day-count conversion used by jdatetime / khayyam. Validated against known
reference dates (see tests/unit/test_jalali.py).

Persian month names and weekday names are included for UI rendering.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

# Persian month names (1-indexed; index 0 unused)
JALALI_MONTHS = [
    "", "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]
JALALI_MONTHS_EN = [
    "", "Farvardin", "Ordibehesht", "Khordad", "Tir", "Mordad", "Shahrivar",
    "Mehr", "Aban", "Azar", "Dey", "Bahman", "Esfand",
]

# Persian weekday names (Saturday = start of week in Iran)
JALALI_WEEKDAYS = [
    "شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه",
]

_G_DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
_J_DAYS_IN_MONTH = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29]


def _gregorian_is_leap(year: int) -> bool:
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)


def jalali_is_leap(year: int) -> bool:
    """33-year cycle leap rule used by the civil Iranian calendar."""
    return ((year + 2346) * 682) % 2816 < 682


def gregorian_to_jalali(g_y: int, g_m: int, g_d: int) -> tuple[int, int, int]:
    """Convert a Gregorian (year, month, day) to Jalali (year, month, day)."""
    gy = g_y - 1600
    gm = g_m - 1
    gd = g_d - 1

    g_day_no = 365 * gy + (gy + 3) // 4 - (gy + 99) // 100 + (gy + 399) // 400
    for i in range(gm):
        g_day_no += _G_DAYS_IN_MONTH[i]
    if gm > 1 and _gregorian_is_leap(g_y):
        g_day_no += 1
    g_day_no += gd

    j_day_no = g_day_no - 79
    j_np = j_day_no // 12053
    j_day_no %= 12053

    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
    j_day_no %= 1461

    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365

    for i in range(11):
        if j_day_no < _J_DAYS_IN_MONTH[i]:
            jm = i + 1
            jd = j_day_no + 1
            break
        j_day_no -= _J_DAYS_IN_MONTH[i]
    else:
        jm = 12
        jd = j_day_no + 1

    return jy, jm, jd


def jalali_to_gregorian(j_y: int, j_m: int, j_d: int) -> tuple[int, int, int]:
    """Convert a Jalali (year, month, day) to Gregorian (year, month, day)."""
    jy = j_y - 979
    jm = j_m - 1
    jd = j_d - 1

    j_day_no = 365 * jy + (jy // 33) * 8 + (jy % 33 + 3) // 4
    for i in range(jm):
        j_day_no += _J_DAYS_IN_MONTH[i]
    j_day_no += jd

    g_day_no = j_day_no + 79

    gy = 1600 + 400 * (g_day_no // 146097)
    g_day_no %= 146097

    leap = True
    if g_day_no >= 36525:
        g_day_no -= 1
        gy += 100 * (g_day_no // 36524)
        g_day_no %= 36524
        if g_day_no >= 365:
            g_day_no += 1
        else:
            leap = False

    gy += 4 * (g_day_no // 1461)
    g_day_no %= 1461

    if g_day_no >= 366:
        leap = False
        g_day_no -= 1
        gy += g_day_no // 365
        g_day_no %= 365

    gd_months = _G_DAYS_IN_MONTH[:]
    if leap:
        gd_months[1] = 29
    for i in range(12):
        if g_day_no < gd_months[i]:
            gm = i + 1
            gd = g_day_no + 1
            break
        g_day_no -= gd_months[i]
    else:
        gm = 12
        gd = g_day_no + 1

    return gy, gm, gd


# ── Convenience helpers operating on datetime.date and ISO/Jalali strings ─────

def date_to_jalali_str(d: date, sep: str = "/") -> str:
    """datetime.date → 'YYYY/MM/DD' Jalali string."""
    jy, jm, jd = gregorian_to_jalali(d.year, d.month, d.day)
    return f"{jy:04d}{sep}{jm:02d}{sep}{jd:02d}"


def jalali_str_to_date(jalali: str) -> Optional[date]:
    """
    'YYYY/MM/DD' (or with - . or Persian digits) Jalali string → datetime.date.
    Returns None if unparseable.
    """
    from services.core.localization.digits import normalize_digits
    cleaned = normalize_digits(jalali).strip()
    parts = [p for p in __import__("re").split(r"[/\-.]", cleaned) if p]
    if len(parts) != 3:
        return None
    try:
        jy, jm, jd = int(parts[0]), int(parts[1]), int(parts[2])
        # Handle 2-digit Jalali years (e.g. 80 → 1380)
        if jy < 100:
            jy += 1300 if jy >= 50 else 1400
        if not (1 <= jm <= 12 and 1 <= jd <= 31):
            return None
        gy, gm, gd = jalali_to_gregorian(jy, jm, jd)
        return date(gy, gm, gd)
    except (ValueError, TypeError):
        return None


def jalali_pretty(d: date, lang: str = "fa") -> str:
    """Human-readable Jalali date, e.g. '۱۵ خرداد ۱۴۰۳' (fa) or '15 Khordad 1403' (en)."""
    from services.core.localization.digits import to_persian_digits
    jy, jm, jd = gregorian_to_jalali(d.year, d.month, d.day)
    if lang == "fa":
        return f"{to_persian_digits(jd)} {JALALI_MONTHS[jm]} {to_persian_digits(jy)}"
    return f"{jd} {JALALI_MONTHS_EN[jm]} {jy}"


def age_from_dob(dob: date, today: Optional[date] = None) -> int:
    """Calendar-agnostic age in years (age is identical in both calendars)."""
    today = today or date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
