"""Official FDA/NFI export → catalog record mapping (CSV path, no pandas needed)."""
from decimal import Decimal

from services.core.drug_catalog.excel_import import records_from_file


def test_reads_fda_style_csv_with_persian_headers_and_digits(tmp_path):
    # Headers as they appear in the real export: spaces + ZWNJ (نیم‌فاصله);
    # values with Persian digits + thousands separators.
    csv = tmp_path / "darou.csv"
    csv.write_text(
        "کد فرآورده,نام فرآورده,ماده موثره,دوز,شکل دارویی,قیمت مصرف‌کننده,تولیدکننده,نوع\n"
        "1234567890123456,آتورواستاتین ۲۰,atorvastatin,20mg,tablet,\"۹۲٬۰۰۰\",تی‌دی فارما,دارو\n"
        "9876543210000001,ویتامین D3,cholecalciferol,1000IU,softgel,120000,یورو ویتال,مکمل\n",
        encoding="utf-8",
    )
    recs = records_from_file(csv)
    assert len(recs) == 2
    a = next(r for r in recs if r.irc == "1234567890123456")
    assert a.generic_name == "atorvastatin"
    assert a.announced_price == Decimal("92000")      # Persian digits + ٬ parsed
    assert a.strength == "20mg" and a.dosage_form == "tablet"
    from services.core.pricing_ir.engine import ItemCategory
    supp = next(r for r in recs if r.irc == "9876543210000001")
    assert supp.category == ItemCategory.SUPPLEMENT    # 'مکمل' → supplement


def test_ingredient_key_matches_after_import(tmp_path):
    csv = tmp_path / "d.csv"
    csv.write_text("irc,name,generic,strength,form,price\n"
                   "X1,Foo,Metformin,500 MG,Tablet,8000\n", encoding="utf-8")
    r = records_from_file(csv)[0]
    from services.core.drug_catalog.schema import ingredient_key
    assert r.ingredient_key == ingredient_key("metformin", "500mg", "tablet")
