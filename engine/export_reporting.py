"""Offline export of cached filing context; never called at question time.

The raw directory and optional reviewed source annotations are explicit inputs.
This exports observations, not corrected facts or inferred restatements.
"""
import argparse
from datetime import date, timedelta
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from zipfile import ZipFile

from .structured import amount
from nlg.lineage import Reporting

HERE = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def export(raw_directory, output, reviews=None, database=HERE / "data/finance_public.db"):
    raw_directory = Path(raw_directory)
    review_data = json.loads(Path(reviews).read_text())["reviews"] if reviews else []
    inputs = {}

    @lru_cache(maxsize=16)
    def raw(cik):
        filename = f"{int(cik):010d}.json"
        if raw_directory.is_file():
            with ZipFile(raw_directory) as archive:
                data = archive.read(filename)
        else:
            data = (raw_directory / filename).read_bytes()
        inputs[filename] = hashlib.sha256(data).hexdigest()
        return json.loads(data)

    records, unavailable = {}, {}
    with sqlite3.connect(database) as db:
        facts = db.execute("SELECT fact_id,predicate,value,valid_from,valid_to,source FROM facts").fetchall()
    for identity, predicate, value, start, stop, source in facts:
        if source.startswith("derived:"):
            continue
        try:
            match = re.fullmatch(r"SEC EDGAR (.+?) ([\d-]+), filed (\d{4}-\d{2}-\d{2}) \(([^)]+)\)", source)
            if not match:
                raise ValueError("unknown source format")
            _, accession, filed, concept = match.groups()
            end = start if predicate == "total_assets" else (date.fromisoformat(stop) - timedelta(days=1)).isoformat()
            data = raw(identity.split("-")[0])["facts"]["us-gaap"]
            info = data[concept]
            rows = [r for r in info["units"]["USD"] if r.get("start", r["end"]) == start and r["end"] == end]
            selected = [r for r in rows if r["accn"] == accession and r["filed"] == filed
                        and amount(r["val"]) == amount(json.loads(value))]
            if not selected:
                raise ValueError("stored selection absent from cached observations")
            earliest = min(rows, key=lambda r: (r["filed"], r["accn"]))

            def observation(row, tag=concept, label=info.get("label", concept)):
                return {"value": str(row["val"]), "unit": "USD", "period_start": start, "period_end": end,
                        "reported_on": row["filed"], "document_type": row["form"], "accession": row["accn"],
                        "source": f"SEC EDGAR {row['form']} {row['accn']}, filed {row['filed']} ({tag})",
                        "measure": f"cached XBRL {tag}: {label}"}

            chosen = selected[0]
            report = {"policy": "earliest_cached" if chosen["filed"] == earliest["filed"] else "selected_snapshot",
                      "selected": observation(chosen), "earliest": observation(earliest),
                      "compatibility": "same_measure" if chosen == earliest else "unverified",
                      "restatement": "not_established"}
            for review in review_data:
                key = (accession, earliest["accn"], concept, start, end)
                if key != tuple(review[k] for k in ("selected_accession", "earliest_accession", "concept", "period_start", "period_end")):
                    continue
                if amount(review["selected_value"]) != amount(json.loads(value)) or amount(review["earliest_value"]) != amount(earliest["val"]):
                    raise ValueError("source review values no longer match")
                report["selected"]["measure"] = review["selected_measure"]
                report["earliest"]["measure"] = review["earliest_measure"]
                report.update({k: review[k] for k in ("compatibility", "restatement", "review", "review_sources")})
                if review.get("additional_concept"):
                    tag = review["additional_concept"]
                    candidates = [r for r in data[tag]["units"]["USD"] if r.get("start") == start and r["end"] == end
                                  and r["accn"] == earliest["accn"]]
                    if not candidates or len({r["val"] for r in candidates}) != 1:
                        raise ValueError("ambiguous source-context observation")
                    report["review_observations"] = [observation(candidates[0], tag, data[tag]["label"])]
            records[identity] = Reporting.model_validate(report).model_dump()
        except (ValueError, KeyError, TypeError, OSError) as exc:
            unavailable[identity] = str(exc)
    result = {"version": 1, "database_sha256": digest(database), "raw_sha256": inputs,
              "reviews_sha256": digest(reviews) if reviews else None, "records": records, "unavailable": unavailable}
    Path(output).write_text(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    return {"reporting_records": len(records), "unavailable": len(unavailable), "derived": len(facts) - len(records) - len(unavailable)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", required=True)
    parser.add_argument("--reviews")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.raw, args.output, args.reviews)))


if __name__ == "__main__":
    main()
