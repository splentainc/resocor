"""Shared SQLite chatter in the domain-first phrase lookup pipeline."""
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
import re

from .store import load

# Only these separators may be crossed at an utterance edge. Symbols such as
# '/', '%', '+' and '-' can carry meaning and are never skipped.
SEPARATORS = frozenset(",.!?;:")
TOKEN = re.compile(r"\w+|[^\w\s]", re.UNICODE)


@dataclass(frozen=True)
class Match:
    start: int
    end: int
    text: str
    phrase: str
    role: str
    context: str
    response: str | None

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Lookup:
    matches: tuple[Match, ...]
    remaining: tuple[tuple[int, int, str], ...]
    response: str | None


class Chatter:
    """Shared SQLite vocabulary; no domain names, metrics, operations or model.

    protected contains half-open character spans supplied by a domain parser.
    Every overlap vetoes the ENTIRE chatter phrase, including a longer phrase
    with only one protected word. No fuzzy, regex or substring phrase matching.
    """

    def __init__(self, path=None):
        self.path = Path(path) if path else Path(__file__).with_name("generic.db")
        data = load(self.path)
        entries, seen = [], set()
        for row in data["entries"]:
            phrase = row["phrase"]
            key = tuple(m.group().casefold() for m in TOKEN.finditer(phrase))
            contexts = frozenset(row["contexts"])
            if (not key or phrase != phrase.strip().casefold() or key in seen
                    or not contexts or not contexts <= {"prefix", "suffix", "standalone"}
                    or row["role"] not in {"greeting", "gratitude", "farewell", "politeness", "request_frame"}
                    or row.get("review") != "author-reviewed"
                    or (row.get("response") is not None and not isinstance(row["response"], str))):
                raise ValueError(f"invalid or unreviewed chatter entry: {phrase!r}")
            seen.add(key)
            entries.append((key, row))
        self.entries = tuple(sorted(entries, key=lambda pair: -len(pair[0])))
        self.lengths = tuple(sorted({len(key) for key, _ in entries}, reverse=True))

    def lookup(self, text, *, protected=()):
        if not isinstance(text, str):
            raise TypeError("chatter text must be a string")
        toks = [(m.group(), m.start(), m.end()) for m in TOKEN.finditer(text)]
        low = [s.casefold() for s, _, _ in toks]
        protected = tuple(protected)
        matches, used = [], set()

        def separator(index):
            _, a, b = toks[index]
            return low[index] in SEPARATORS and not any(a < d and c < b for c, d in protected)

        def hit(start, end, context):
            for key, row in self.entries:
                if len(key) != end - start or context not in row["contexts"]:
                    continue
                if tuple(low[start:end]) != key:
                    continue
                ch0, ch1 = toks[start][1], toks[end - 1][2]
                # Tokens inside a phrase must be separated only by whitespace.
                if any(text[toks[i][2]:toks[i + 1][1]].strip() for i in range(start, end - 1)):
                    continue
                if any(ch0 < b and a < ch1 for a, b in protected):
                    continue
                matches.append(Match(ch0, ch1, text[ch0:ch1], row["phrase"],
                                     row["role"], context, row.get("response")))
                used.update(range(start, end))
                return True
            return False

        lo, hi = 0, len(toks)
        while lo < hi and separator(lo):
            lo += 1
        while hi > lo and separator(hi - 1):
            hi -= 1
        if lo < hi and hit(lo, hi, "standalone"):
            return Lookup(tuple(matches), (), matches[0].response)
        while lo < hi:
            found = next((lo + size for size in self.lengths
                          if lo + size <= hi and hit(lo, lo + size, "prefix")), None)
            if found is None:
                break
            lo = found
            while lo < hi and separator(lo):
                lo += 1
        while hi > lo:
            found = next((hi - size for size in self.lengths
                          if hi - size >= lo and hit(hi - size, hi, "suffix")), None)
            if found is None:
                break
            hi = found
            while hi > lo and separator(hi - 1):
                hi -= 1
        remaining = tuple((a, b, s) for i, (s, a, b) in enumerate(toks)
                          if i not in used and not separator(i))
        matches.sort(key=lambda m: m.start)
        response = next((m.response for m in reversed(matches) if m.response), None) if not remaining else None
        return Lookup(tuple(matches), remaining, response)


@lru_cache(maxsize=1)
def generic():
    return Chatter()
