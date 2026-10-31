"""Read-only SQLite generic chatter, independent of any domain database."""
from pathlib import Path
import sqlite3

CONTEXTS = ("standalone", "prefix", "suffix")


def load(path):
    """Load persisted rows only; missing/invalid databases fail closed.

    mode=ro forbids accidental creation or modification. Connections are closed
    after loading, just as for the engine's domain glossary.
    """
    con = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        if con.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("unsupported chatter database schema")
        metadata = dict(con.execute("SELECT key, value FROM metadata"))
        if metadata.get("version") != "1" or metadata.get("language") != "en":
            raise ValueError("unsupported chatter dataset")
        rows = con.execute("""SELECT user_phrase, role, allow_standalone,
                              allow_prefix, allow_suffix, response, review, source
                              FROM generic_chatter ORDER BY id""").fetchall()
        entries = []
        for phrase, role, standalone, prefix, suffix, response, review, source in rows:
            flags = (standalone, prefix, suffix)
            if any(flag not in (0, 1) for flag in flags) or not source:
                raise ValueError(f"invalid chatter database row: {phrase!r}")
            entries.append({"phrase": phrase, "role": role,
                            "contexts": [name for name, flag in zip(CONTEXTS, flags) if flag],
                            "response": response, "review": review})
        if not entries:
            raise ValueError("empty chatter dataset")
        return {"version": 1, "language": "en", "review_scope": metadata.get("review_scope", ""),
                "entries": entries}
    finally:
        con.close()
