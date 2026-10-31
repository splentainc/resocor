"""Facts from the SQLite fact database, one Fact per row, indexed by (company, predicate).

Companies are the engine's names (the keys of domain.json `db_names`), not the legal names stored in the database.
"""
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "data", "finance_public.db")
DOMAIN = os.path.join(HERE, "data", "domain.json")


@dataclass(frozen=True)
class Fact:
    fact_id: str
    company: str
    predicate: str
    value: object
    valid_from: date
    valid_to: Optional[date]  # None means still current
    source: str

    def valid_at(self, d: date) -> bool:
        return d >= self.valid_from and (self.valid_to is None or d < self.valid_to)


def company_db_names() -> dict:
    """{engine company name: name in the database}."""
    return json.load(open(DOMAIN))["db_names"]


def _date(s):
    return date.fromisoformat(s) if s else None


def load_facts(db_path: str = DB) -> dict:
    """{(company, predicate): [Fact, ...]} in database order."""
    to_company = {v: k for k, v in company_db_names().items()}
    out: dict = {}
    for fact_id, subject, predicate, value, valid_from, valid_to, source in sqlite3.connect(db_path).execute(
            "SELECT fact_id, subject, predicate, value, valid_from, valid_to, source FROM facts"):
        company = to_company[subject]
        out.setdefault((company, predicate), []).append(Fact(fact_id, company, predicate, json.loads(value), _date(valid_from), _date(valid_to), source))
    return out
