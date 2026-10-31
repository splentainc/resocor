"""Offline JSON-to-SQLite migration; runtime has no JSON fallback.

python -m chatter.migrate SOURCE.json DESTINATION.db
Existing destinations are never overwritten. Supply a versioned JSON dataset.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from .store import CONTEXTS


def migrate(source, destination):
    source, destination = Path(source), Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    raw = source.read_bytes()
    data = json.loads(raw)
    if data.get("version") != 1 or data.get("language") != "en":
        raise ValueError("unsupported chatter migration source")
    descriptor, temporary = tempfile.mkstemp(prefix=".chatter-", suffix=".db", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(temporary)
    try:
        con = sqlite3.connect(temporary)
        try:
            con.executescript(Path(__file__).with_name("schema.sql").read_text())
            metadata = {"version": "1", "language": "en", "review_scope": data.get("review_scope", ""),
                        "source_json_sha256": hashlib.sha256(raw).hexdigest()}
            with con:
                con.executemany("INSERT INTO metadata VALUES (?, ?)", metadata.items())
                for i, row in enumerate(data["entries"], 1):
                    contexts = row["contexts"]
                    if not contexts or len(contexts) != len(set(contexts)) or set(contexts) - set(CONTEXTS):
                        raise ValueError(f"invalid chatter contexts: {contexts!r}")
                    con.execute("INSERT INTO generic_chatter VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (i, row["phrase"], row["role"], *(int(c in contexts) for c in CONTEXTS),
                                 row.get("response"), row["review"], "generic.json migration"))
        finally:
            con.close()
        # Validate with the actual runtime loader before publishing any file.
        from . import Chatter
        loaded = Chatter(temporary)
        os.link(temporary, destination)  # atomic publish; fails if another writer created destination
        return {"entries": len(loaded.entries), "source_json_sha256": metadata["source_json_sha256"],
                "database_sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.destination), indent=2))


if __name__ == "__main__":
    main()
