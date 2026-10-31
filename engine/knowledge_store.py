"""Phrase meanings and composition templates share the glossary table."""
import json
import sqlite3
from seed_vocabulary import attach


def load(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = list(con.execute("SELECT user_phrase, valid_phrase, phrase_type, match, source, reviewed FROM glossary ORDER BY id"))
        names = ("user_phrase", "valid_phrase", "phrase_type", "match", "source", "reviewed")
        phrases = [dict(zip(names, row)) for row in rows if row[2] != "composition" and not row[2].startswith("segment:")]
        templates = {row[0]: json.loads(row[1]) for row in rows if row[2] == "composition"}
        return attach(con, phrases), templates
    finally:
        con.close()


def add_composition(path, signature, template, source):
    with sqlite3.connect(path) as con:
        row = con.execute("SELECT valid_phrase FROM glossary WHERE phrase_type = 'composition' AND user_phrase = ?", (signature,)).fetchone()
        if row:
            return "same" if json.loads(row[0]) == template else "conflict"
        raise RuntimeError("new reference-derived composition templates are disabled")


def migrate(path):
    """Offline, lossless move of the former compositions table; conflicts abort."""
    with sqlite3.connect(path) as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='compositions'").fetchone():
            return 0
        rows = list(con.execute("SELECT signature, template, source, reviewed FROM compositions"))
        for sig, template, source, reviewed in rows:
            old = con.execute("SELECT valid_phrase FROM glossary WHERE phrase_type='composition' AND user_phrase=?", (sig,)).fetchone()
            if old and json.loads(old[0]) != json.loads(template):
                raise ValueError(f"conflicting composition: {sig}")
            if not old:
                con.execute("INSERT INTO glossary (user_phrase,valid_phrase,phrase_type,match,source,reviewed) VALUES (?,?,'composition','phrase',?,?)",
                            (sig, template, source, reviewed))
        con.execute("DROP TABLE compositions")
        return len(rows)


def load_phrases(path):
    """Segmentation reads lexical knowledge only, never composition/reference trees."""
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
        rows = con.execute("SELECT user_phrase,valid_phrase,phrase_type,match,source,reviewed FROM glossary WHERE phrase_type != 'composition' AND phrase_type NOT LIKE 'segment:%' ORDER BY id")
        names = ("user_phrase", "valid_phrase", "phrase_type", "match", "source", "reviewed")
        return attach(con, [dict(zip(names, row)) for row in rows])
