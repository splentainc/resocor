"""Each company's fiscal years and quarters, as stored in the fact database.

{company: {("FY", year): (start, end), ("Q", year, quarter): (start, end)}}, dates as ISO strings. Fiscal years are named by the
calendar year in which they end, and the supplied company calendar determines the dates.
"""
import re
import sqlite3

from fact_store import DB, company_db_names


def load_calendar(db_path: str = DB) -> dict:
    db = sqlite3.connect(db_path)
    out: dict = {}
    for company, db_name in company_db_names().items():
        periods = out[company] = {}
        for label, kind, start, end in db.execute("SELECT label, kind, start, end FROM fiscal_periods WHERE company = ?", (db_name,)):
            m = re.fullmatch(r"FY(\d{4})(?: Q([1-4]))?", label)
            periods[("FY", int(m[1])) if kind == "FY" else ("Q", int(m[1]), int(m[2]))] = (start, end)
    return out
