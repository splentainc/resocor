"""Generic phrase composition over glossary-typed spans; vocabulary stays in SQLite."""
import re
from quarter_composition import compose_quarters
from scope_binding import bind_entities, bind_periods
from construction_rules import compose_comparators

PERIOD_TYPES = {"number_word", "period_year", "period_date", "period_marker", "period_unit", "period_rel", "ordinal", "period_fy", "period_quarter", "period_yr2", "period_date_num"}
PERIOD_ANCHOR = {"period_year", "period_date", "period_fy", "period_quarter", "period_yr2", "period_date_num", "period_rel"}  # a period phrase needs one of these


class PhraseRules:
    def __init__(self, view, gloss):
        self.view, self.gloss = view, gloss

    def compose(self, items, question):
        for rule in (self._syntax, self._dates, self._references, self._quarter_sets, self._scopes, self._comparators, self._periods, self._context, self._qualifiers, self._ranges,
                     self._bounds, self._exclusions, self._negations,
                     self._premise, self._retext):
            items = rule(items, question)
        return items

    def _references(self, items, question):
        return bind_entities(self, items, question)

    def _scopes(self, items, question):
        return bind_periods(self, items, question)

    def _comparators(self, items, question):
        return compose_comparators(self, items, question)

    def _quarter_sets(self, items, question):
        return compose_quarters(self, items, question)

    def _syntax(self, items, question):
        """Resolve language syntax before grouping; no reference segments are read."""
        typ = lambda n: self.view(items[n])["phrase_type"] if 0 <= n < len(items) else None
        word = lambda n: question[items[n]["ch0"]:items[n]["ch1"]].casefold()
        has_domain = any(typ(n) == "entity" for n in range(len(items))) and any(typ(n) == "metric" for n in range(len(items)))
        request_verbs = {"show", "list", "display", "get"}
        frame_words = {"can", "could", "would", "i", "we", "you", "please", "for", "in", "between", "and", "from", "to", "through", ",", ":"}
        temporal = PERIOD_TYPES | {"month", "number", "range_marker", "bound"}
        movement_words = {r["user_phrase"].casefold() for r in self.gloss if r["phrase_type"] == "change_series"}
        for k, item in enumerate(items):
            raw = word(k)
            if has_domain and raw in request_verbs:
                prefix = list(range(k))
                if all(typ(n) in temporal or (typ(n) == "grammar" and word(n) in frame_words) for n in prefix):
                    item.update(ptype="grammar", seg_type="filler", consumed=True)
                    for n in prefix:
                        if typ(n) == "grammar" and word(n) in {"can", "could", "would", "i", "we", "you", "please"}:
                            items[n].update(seg_type="filler", consumed=True)
            # Productive inflection recovers a known motion verb, rather than
            # inserting a missing word from an expected annotation.
            if typ(k) == "unlisted" and any(raw + ending in movement_words for ending in ("ed", "d")):
                item.update(ptype="change_series", seg_type="change")
            # Counted completeness can apply to any standard calendar unit.
            if raw in {"months", "weeks", "days"} and typ(k - 1) == "number" and typ(k - 2) == "coverage":
                item.update(ptype="step", valid=raw[:-1], seg_type="period")
            # Prefix subtraction has reversed operand order. Preserve its
            # operator spans, but do not guess a tree using an infix template.
            if raw == "subtract" and not any(typ(n) in {"entity", "metric"} for n in range(k)):
                item.update(ptype="unsupported_op", seg_type="operator")
                for j in range(k + 1, len(items)):
                    if word(j) == "from" and any(typ(n) == "metric" for n in range(k + 1, j)):
                        items[j].update(ptype="relation_link", seg_type="operator")
                        break
        return items


    def _dates(self, items, question):
        """month [day] [,] year, or month year, is one date: 'June 30, 2024'. A month that is not followed by a year stays a lone month."""
        t = lambda n: self.view(items[n])["phrase_type"] if n < len(items) else None
        out, k = [], 0
        while k < len(items):
            if t(k) == "month":
                j = k + 1
                if t(j) == "number" and 1 <= int(question[items[j]["ch0"]:items[j]["ch1"]]) <= 31:
                    j += 1
                if t(j) == "grammar" and question[items[j]["ch0"]:items[j]["ch1"]] == ",":
                    j += 1
                if t(j) == "period_year":
                    run = items[k:j + 1]
                    out.append({"start": run[0]["start"], "end": run[-1]["end"], "ch0": run[0]["ch0"], "ch1": run[-1]["ch1"], "row": None, "ptype": "period_date"})
                    k = j + 1
                    continue
            out.append(items[k])
            k += 1
        return out


    def _span(self, run, ptype, **extra):
        return {"start": run[0]["start"], "end": run[-1]["end"], "ch0": run[0]["ch0"], "ch1": run[-1]["ch1"], "row": None, "ptype": ptype, **extra}


    def _anchored(self, views):
        """True when the run states a time on its own: a year, quarter, date, 'latest', or last/next together with a unit ('last year')."""
        types = {v["phrase_type"] for v in views}
        if types & (PERIOD_ANCHOR - {"period_rel"}):
            return True
        if {"ordinal", "period_unit"} <= types:
            return True
        return any(v["phrase_type"] == "period_rel" and (v["valid_phrase"] == "latest" or "period_unit" in types or ({"step", "step_modifier"} & types and {"number_word", "number"} & types)) for v in views)


    def _periods(self, items, question):
        """A run of adjacent period words with at least one anchor (a year, quarter, date or relative word) is one period phrase:
        'fiscal 2023', 'Q1 FY2023', 'quarter ended June 2022', 'last year'. One 'of' or 'in' may join two parts: 'first quarter of 2023'."""
        t = lambda n: self.view(items[n])["phrase_type"] if 0 <= n < len(items) else None
        word = lambda n: question[items[n]["ch0"]:items[n]["ch1"]].lower()
        out, k = [], 0
        while k < len(items):
            if t(k) in PERIOD_TYPES:
                j = k
                while True:
                    if t(j + 1) in PERIOD_TYPES:
                        j += 1
                    elif t(j + 1) == "number" and t(j) == "period_rel":  # "next 4 quarters"
                        j += 1
                    elif t(j + 1) == "step" and word(j + 1) in ("quarters", "years") and t(j) in ("number_word", "number"):  # "coming two quarters"
                        j += 1
                    elif t(j + 1) == "period_marker" and word(j + 1) == "end" and t(j) not in ("period_marker",):  # "FY2024 end"
                        j += 1
                    elif t(j + 1) == "grammar" and word(j + 1) in ("of", "in", "that") and t(j + 2) in PERIOD_TYPES and t(j) in {"period_unit", "ordinal", "period_quarter", "period_marker"}:
                        j += 2
                    else:
                        break
                if self._anchored([self.view(it) for it in items[k:j + 1]]):
                    counted = any(self.view(it)["phrase_type"] in ("number_word", "number") for it in items[k:j + 1])  # a count makes it a span of several periods, a different shape
                    out.append(self._span(items[k:j + 1], "period_span_n" if counted else "period_phrase", future=any(self.view(it)["valid_phrase"] == "next" for it in items[k:j + 1])))
                    k = j + 1
                    continue
            out.append(items[k])
            k += 1
        return out


    def _qualifiers(self, items, question):
        """'quarterly' next to one named quarter ('quarterly net income for Q1 2024') says which figure, not a series: it is a modifier."""
        t = lambda it: self.view(it)["phrase_type"]
        pers = [it for it in items if t(it) in ("period_phrase", "period_span_n", "period_range", "period_bound", "step", "coverage")]
        if len(pers) == 1 and t(pers[0]) == "period_phrase" and re.search(r"\bq[1-4]\b|\bquarter\b", question[pers[0]["ch0"]:pers[0]["ch1"]], re.I):
            for it in items:
                if t(it) == "step_modifier":
                    it["ptype"] = "modifier"
        return items


    def _year_of(self, text):
        """The year a period phrase names: the last 4-digit number, else the last 2-digit one (fy23 -> 2023); None when it names none."""
        m = re.findall(r"(?<!\d)(\d{4})(?!\d)", text) or [("20" + d) for d in re.findall(r"(?<!\d)(\d{2})(?!\d)", text)]
        return int(m[-1]) if m else None


    def _ranges(self, items, question):
        """P1 - P2, P1 through P2, from P1 to P2, between P1 and P2 are one range of two period phrases."""
        t = lambda n: self.view(items[n])["phrase_type"] if 0 <= n < len(items) else None
        word = lambda it: question[it["ch0"]:it["ch1"]].lower()
        out, k = [], 0
        while k < len(items):
            if t(k) == "period_phrase" and t(k + 2) == "period_phrase":
                conn = word(items[k + 1])
                lead = out[-1] if out and word(out[-1]) in ("between", "from", "over") else None
                versus = conn in ("vs", "versus")  # "P1 vs P2": a range from the earlier period to the later one, whichever comes first
                ya, yb = (self._year_of(word(items[k])), self._year_of(word(items[k + 2]))) if versus else (None, None)
                coordinated = conn == "and" and any(t(n) in ("step", "step_modifier", "coverage") for n in range(k)) and not any(t(n) == "negation" for n in range(k))
                if conn in ("-", "through", "to") or (versus and ya and yb and ya != yb) or coordinated or (conn == "and" and lead and word(lead) in ("between", "over")):
                    if lead:
                        out.pop()
                    run = [lead or items[k], items[k + 2]]
                    a, b = (items[k + 2], items[k]) if versus and ya > yb else (items[k], items[k + 2])
                    out.append(self._span(run, "period_range", from_ch=(a["ch0"], a["ch1"]), to_ch=(b["ch0"], b["ch1"])))
                    k += 3
                    continue
            out.append(items[k])
            k += 1
        return out


    def _context(self, items, question):
        """Resolve grammatical roles from typed neighbours; no record IDs or sentence templates."""
        t = lambda n: self.view(items[n])["phrase_type"] if 0 <= n < len(items) else None
        v = lambda n: self.view(items[n])["valid_phrase"] if 0 <= n < len(items) else None
        for k, it in enumerate(items):
            if t(k) == "range_marker" and t(k + 1) == "entity" and any(t(n) == "arithmetic_diff" for n in range(k)):
                it["seg_type"], it["consumed"] = "filler", True
            if t(k) == "grammar" and v(k) == "division_link" and any(t(n) == "arithmetic" and v(n) == "div" for n in range(k)):
                it["seg_type"] = "operator"
            if t(k) == "count_query":
                j = k + 1
                while t(j) == "grammar":
                    j += 1
                it["ptype"] = "unsupported_aggregation" if t(j) in ("period_phrase", "period_span_n", "period_unit", "step") else "grammar"
                if t(j) == "amount_unit" and "change" in (t(n) for n in range(len(items))) and not any(t(n) == "change_pct" for n in range(len(items))):
                    it["consumed"] = True  # amount question represented by Change(abs)
            if t(k) == "hypothetical_aux" and k == 0 and "," in question:
                it["ptype"], it["valid"] = "hypothetical", "hypothetical"
            elif t(k) == "hypothetical_aux":
                it["consumed"] = True  # An ordinary auxiliary is not an inverted premise.
            if t(k) == "grouping_cue":
                it["ptype"] = "grammar"  # only an exact reviewed frame may explain the unused sense
            if t(k) == "amount_unit":
                it["ptype"] = "change_units" if any(t(n) in ("change", "change_pct") for n in range(k)) else "grammar"
                if t(k) == "grammar" and any(t(n) == "change" for n in range(len(items))) and not any(t(n) == "change_pct" for n in range(len(items))):
                    it["consumed"] = True
            if t(k) == "modifier_tail":
                p = k - 1
                while t(p) == "grammar" and v(p) == "metric_wrapper":
                    p -= 1
                if t(p) in ("modifier", "step_modifier", "period_unit"):
                    it["consumed"], it["seg_type"] = True, "filler"
            if t(k) == "period_unit" and t(k - 1) == "aggregation" and v(k - 1) in ("max", "min"):
                it["ptype"], it["valid"] = "step_which", "period"
            if t(k) == "aggregation" and t(k + 1) == "period_phrase" and any(t(n) in ("aggregation", "step", "step_modifier") for n in range(k)):
                it["ptype"], it["consumed"] = "grammar", True
            if t(k) == "arithmetic" and v(k) == "sub" and any(t(n) in ("step", "step_modifier") for n in range(k)) and t(k + 1) in ("period_phrase", "grammar"):
                j = k + 1
                while t(j) == "grammar":
                    j += 1
                if t(j) == "period_phrase":
                    it["ptype"], it["valid"] = "negation", "except"
        return items

    def _bounds(self, items, question):
        """before, prior to, after, since, until + a period phrase is one bound."""
        out, k = [], 0
        while k < len(items):
            if (k + 3 < len(items) and question[items[k]["ch0"]:items[k]["ch1"]].casefold() in {"on", "at"}
                    and question[items[k + 1]["ch0"]:items[k + 1]["ch1"]].casefold() == "or"
                    and self.view(items[k + 2])["phrase_type"] == "bound"
                    and self.view(items[k + 3])["phrase_type"] == "period_phrase"):
                direction = self.view(items[k + 2])["valid_phrase"]
                if direction in {"lt", "gt"}:
                    out.append(self._span(items[k:k + 4], "period_bound", op=direction + "e",
                                          value_ch=(items[k + 3]["ch0"], items[k + 3]["ch1"])))
                    k += 4
                    continue

            if self.view(items[k])["phrase_type"] == "period_phrase" and k + 1 < len(items) and self.view(items[k + 1])["phrase_type"] == "bound_after":
                lead = out.pop() if out and question[out[-1]["ch0"]:out[-1]["ch1"]].lower() == "from" else None
                out.append(self._span(([lead] if lead else []) + items[k:k + 2], "period_bound", op=self.view(items[k + 1])["valid_phrase"], value_ch=(items[k]["ch0"], items[k]["ch1"])))
                k += 2
                continue
            if self.view(items[k])["phrase_type"] == "bound" and k + 1 < len(items) and self.view(items[k + 1])["phrase_type"] == "period_phrase":
                out.append(self._span(items[k:k + 2], "period_bound", op=self.gloss[items[k]["row"]]["valid_phrase"], value_ch=(items[k + 1]["ch0"], items[k + 1]["ch1"])))
                k += 2
                continue
            out.append(items[k])
            k += 1
        return out


    def _exclusions(self, items, question):
        """except / apart from / excluding / leaving out followed by period phrases ('Q3', 'q2 and q3') exclude those periods from a series."""
        t = lambda n: self.view(items[n])["phrase_type"] if 0 <= n < len(items) else None
        word = lambda n: question[items[n]["ch0"]:items[n]["ch1"]].lower()
        for k, it in enumerate(items):
            if t(k) != "negation" or self.view(it)["valid_phrase"] != "except":
                continue
            j, found = k + 1, []
            while True:
                while t(j) == "grammar" and word(j) in ("the", "a", "an"):
                    j += 1
                if t(j) != "period_phrase":
                    break
                found.append(j)
                j += 1
                if t(j) == "grammar" and word(j) in ("and", ","):
                    j += 1
                else:
                    break
            if found:
                it["consumed"] = True
                for n in found:
                    items[n]["ptype"] = "period_except"
        return items


    def _premise(self, items, question):
        """if / suppose / what if / hypothetically + a clause: the clause, up to the next comma, dash or question mark, is a verbatim premise.
        Its words stay available as slots (numbered apart from the question's own) but are not interpreted and not part of the signature."""
        t = lambda n: self.view(items[n])["phrase_type"] if 0 <= n < len(items) else None
        word = lambda n: question[items[n]["ch0"]:items[n]["ch1"]]
        for k, it in enumerate(items):
            if t(k) != "hypothetical" or self.view(it)["valid_phrase"] != "hypothetical":
                continue
            j = k + 1
            while j < len(items) and (t(j) == "hypothetical" or word(j) == ","):
                j += 1
            e = j
            while e < len(items) and word(e) not in (",", ";", "-", "--", "—", "–", "?"):
                e += 1
            if e > j:
                it["premise"] = (items[j]["ch0"], items[e - 1]["ch1"])
                for n in range(j, e):
                    items[n]["in_premise"] = True
                for echo in items:
                    if not echo.get("in_premise") and self.view(echo)["valid_phrase"] == "hypothetical_echo":
                        echo["seg_type"] = "hypothetical"
            break
        return items


    def _negations(self, items, question):
        """not / no / without / excluding followed by a modifier word (annual, quarterly) negates that modifier: 'not the annual figure'.
        A period unit just before the negation, 'the quarter, not the annual figure', is the positive modifier. Other negations stay uncomposed."""
        t = lambda n: self.view(items[n])["phrase_type"] if 0 <= n < len(items) else None
        word = lambda n: question[items[n]["ch0"]:items[n]["ch1"]]
        for k, it in enumerate(items):
            if t(k) != "negation":
                continue
            j = k + 1
            while t(j) == "grammar" and word(j).lower() in ("the", "a", "an", "any"):
                j += 1
            if t(j) in ("modifier", "step_modifier"):
                items[j]["ptype"], items[j]["negated"], it["consumed"] = "modifier", True, True
                for article in items[k + 1:j]:
                    article["consumed"] = True
                p = k - 1
                while p >= 0 and (t(p) == "modifier_tail" or (t(p) == "grammar" and word(p) == ",")):  # "the quarter alone, not ..."
                    p -= 1
                if t(p) == "period_unit":
                    items[p]["ptype"] = "modifier"
        return items


    def _retext(self, items, question):
        for n, it in enumerate(items):  # text runs to the start of the next item, so the segments join back to the question exactly
            it["text"] = question[(0 if n == 0 else it["ch0"]):(items[n + 1]["ch0"] if n + 1 < len(items) else len(question))]
        return items


