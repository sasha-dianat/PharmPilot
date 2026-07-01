"""Drug catalog — IRC-keyed priced product list with active-ingredient grouping.

Powers the reception affordability flow's 'switch to a cheaper brand/generic with
the same active ingredient' lever, and feeds the pricing engine the announced /
invoice prices. Format-agnostic ingestion (see importer.py) loads the official
فهرست رسمی دارویی / NFI export (CSV/Excel/JSON) or the IRC API once credentialed.
"""
