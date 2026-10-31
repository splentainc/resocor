"""Pydantic models for typed question annotations.

validate(data, question) -> Annotation, or raises ValidationError. canonical(annotation) -> a normal form that is equal
for two outputs exactly when their content is equal. Every normalisation applied is recorded in `canonical(...).changes`.
"""
import re
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

SegType = Literal["entity", "metric", "period", "quantity", "operator", "aggregation", "change", "negation", "modifier", "hypothetical", "filler", "unknown"]
NODES = ["Lookup", "Series", "Change", "Arith", "Aggregate", "Compare", "Not", "Forecast", "Hypothetical", "Unsupported", "Quantity", "Bound", "Modifier"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Segment(Strict):
    id: str
    text: str
    type: SegType


class Modifier(Strict):
    node: Literal["Modifier"]
    text: str


class QualNot(Strict):
    node: Literal["Not"]
    expr: Modifier


class MetricObj(Strict):
    name: str
    qualifiers: list[Union[Modifier, QualNot]] = []


class Bound(Strict):
    node: Literal["Bound"]
    op: Literal["lt", "lte", "gt", "gte"]
    value: str


class Quantity(Strict):
    node: Literal["Quantity"]
    text: str


Metric = Union[str, MetricObj]
Edge = Union[str, Bound, None]


class Lookup(Strict):
    node: Literal["Lookup"]
    entity: Optional[str] = None
    metric: Optional[Metric] = None
    period: Optional[str] = None


class QuarterSet(Strict):
    fiscal_year: int = Field(ge=1900, le=2199)
    quarters: list[int]
    selection: Literal["first", "all"]

    @model_validator(mode="after")
    def _finite(self):
        if self.quarters not in ([1], [1, 2], [1, 2, 3], [1, 2, 3, 4]):
            raise ValueError("quarter set must be a finite fiscal prefix")
        if self.selection == "all" and len(self.quarters) != 4:
            raise ValueError("all quarters requires four")
        return self


class Series(Strict):
    quarter_set: Optional[QuarterSet] = None
    node: Literal["Series"]
    entity: Optional[str] = None
    metric: Optional[Metric] = None
    from_: Edge = Field(default=None, alias="from")
    to: Edge = None
    step: Optional[str] = None
    coverage: Optional[str] = None
    except_: list[str] = Field(default_factory=list, alias="except")


class Change(Strict):
    node: Literal["Change"]
    kind: Literal["abs", "pct"]
    of: Lookup
    from_: Optional[str] = Field(default=None, alias="from")
    to: Optional[str] = None
    basis: Optional[str] = None  # verbatim, when the prior period is implied ("yoy")


class Aggregate(Strict):
    node: Literal["Aggregate"]
    fn: Literal["sum", "avg", "min", "max"]
    over: Series
    returns: Literal["value", "period"] = "value"


class Forecast(Strict):
    node: Literal["Forecast"]
    expr: Union[Lookup, Series]
    horizon: Optional[str] = None


class Hypothetical(Strict):
    node: Literal["Hypothetical"]
    premise: list[str]
    expr: Union[Lookup, Series]


class Unsupported(Strict):
    node: Literal["Unsupported"]
    reason: str
    segments: list[str] = []


class Arith(Strict):
    node: Literal["Arith"]
    op: Literal["add", "sub", "div", "mul"]
    left: Union["Tree", Quantity]
    right: Union["Tree", Quantity]


class Compare(Strict):
    node: Literal["Compare"]
    op: Literal["gt", "gte", "lt", "lte", "eq", "max", "min"]
    left: "Tree"
    right: Union["Tree", Quantity]


class TreeNot(Strict):
    node: Literal["Not"]
    expr: "Tree"


Tree = Annotated[Union[Lookup, Series, Change, Arith, Aggregate, Compare, TreeNot, Forecast, Hypothetical, Unsupported], Field(discriminator="node")]
for _m in (Arith, Compare, TreeNot):
    _m.model_rebuild()


def _fix_case(x):
    """Accept 'lookup' for 'Lookup' etc.; field names are exact."""
    if isinstance(x, dict):
        out = {k: _fix_case(v) for k, v in x.items()}
        if isinstance(out.get("node"), str):
            out["node"] = next((n for n in NODES if n.lower() == out["node"].lower()), out["node"])
        return out
    if isinstance(x, list):
        return [_fix_case(i) for i in x]
    return x


class ScopeBinding(Strict):
    kind: Literal['entity', 'fiscal_period']
    source_span: list[int] = Field(min_length=2, max_length=2)
    anchor_span: list[int] = Field(min_length=2, max_length=2)
    entity: str
    fiscal_year: Optional[int] = None
    quarters: Optional[list[int]] = None

    @model_validator(mode='after')
    def _binding(self):
        if self.kind == 'fiscal_period':
            if self.fiscal_year is None or self.quarters not in ([1], [2], [3], [4], [1, 2], [1, 2, 3], [1, 2, 3, 4]):
                raise ValueError('fiscal binding requires an explicit finite period')
        return self


class Annotation(Strict):
    id: int
    segments: list[Segment]
    tree: Tree
    unplaced: list[str] = []
    ignored: list[str] = []  # legacy compatibility; new parsing retains unknowns
    note: str = ""
    bindings: Optional[list[ScopeBinding]] = None

    @field_validator("tree", mode="before")
    @classmethod
    def _case(cls, v):
        return _fix_case(v)

    @model_validator(mode="after")
    def _invariants(self, info: ValidationInfo):
        q = (info.context or {}).get("question")
        if q is None:
            return self
        if "".join(s.text for s in self.segments) != q:
            raise ValueError("segments do not join back to the question")
        for binding in self.bindings or []:
            for lo, hi in (binding.source_span, binding.anchor_span):
                if not 0 <= lo < hi <= len(q):
                    raise ValueError('scope binding span outside question')
        ids = [s.id for s in self.segments]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate segment ids")
        for u in self.unplaced:
            if u not in ids or next(s for s in self.segments if s.id == u).type == "filler":
                raise ValueError(f"unplaced {u} is not a non-filler segment")
        return self


def validate(data: dict, question: str) -> Annotation:
    return Annotation.model_validate(data, context={"question": question})


# ---- canonical form ------------------------------------------------------------------------------------------------

CONNECT = re.compile(r"^(?:as of|across|over|for|in|during|of|the|a|an)\s+")


def leaf(s):
    if s is None:
        return None
    s = re.sub(r"[^\w$%.]+", " ", re.sub(r"['’]s\b", "", s.lower())).strip()
    while CONNECT.match(s):
        s = CONNECT.sub("", s, 1)
    return s or None


def _metric(m):
    if m is None:
        return None
    if isinstance(m, str):
        return {"name": leaf(m), "qualifiers": []}
    quals = []
    for q in m.qualifiers:
        quals.append({"not": leaf(q.expr.text)} if isinstance(q, QualNot) else {"mod": leaf(q.text)})
    return {"name": leaf(m.name), "qualifiers": quals}


def _edge(e):
    if isinstance(e, Bound):
        return {"bound": e.op, "value": leaf(e.value)}
    return leaf(e)


def canon_tree(t):
    n = t.node
    if n == "Lookup":
        return {"node": n, "entity": leaf(t.entity), "metric": _metric(t.metric), "period": leaf(t.period)}
    if n == "Series":
        return {**({"quarter_set": t.quarter_set.model_dump()} if t.quarter_set else {}), "node": n, "entity": leaf(t.entity), "metric": _metric(t.metric), "from": _edge(t.from_), "to": _edge(t.to),
                "step": leaf(t.step), "coverage": leaf(t.coverage), "except": [leaf(x) for x in t.except_]}
    if n == "Change":
        return {"node": n, "kind": t.kind, "of": canon_tree(t.of), "from": leaf(t.from_), "to": leaf(t.to), "basis": leaf(t.basis)}
    if n == "Arith":
        operand = lambda x: {"node": "Quantity", "text": leaf(x.text)} if isinstance(x, Quantity) else canon_tree(x)
        return {"node": n, "op": t.op, "left": operand(t.left), "right": operand(t.right)}
    if n == "Aggregate":
        return {"node": n, "fn": t.fn, "over": canon_tree(t.over), "returns": t.returns}
    if n == "Compare":
        r = {"node": "Quantity", "text": leaf(t.right.text)} if isinstance(t.right, Quantity) else canon_tree(t.right)
        return {"node": n, "op": t.op, "left": canon_tree(t.left), "right": r}
    if n == "Not":
        return {"node": n, "expr": canon_tree(t.expr)}
    if n == "Forecast":
        return {"node": n, "expr": canon_tree(t.expr), "horizon": leaf(t.horizon)}
    if n == "Hypothetical":
        return {"node": n, "premise": [re.sub(r"^(?:had|if|suppose|what if|hypothetically)\s+", "", leaf(p) or "") for p in t.premise], "expr": canon_tree(t.expr)}
    return {"node": "Unsupported", "reason": "-"}


SEG_CONNECT = {"which", "of", "for", "in", "the", "a", "an", "as", "at", "on", "during", "over", "across"}  # normalisation: connectors inside a segment are not content


STEP_PHRASE = re.compile(r"^(?:(?:each|every|per|by)(?: fiscal)? (?:quarter|year)|(?:quarter|year) by (?:quarter|year)|quarterly|yearly|annually|all \d+|quarter|year)$")


def canon_segments(a: Annotation):
    """Typed non-filler segments as (type, leaf text), adjacent segments of the same type merged, order kept, connectors dropped."""
    explicit_aggregation = any(s.type == "aggregation" and any(
        w not in SEG_CONNECT for w in (leaf(s.text) or "").split()) for s in a.segments)
    out = []
    for s in a.segments:
        if s.type == "filler":
            continue
        t = " ".join(w for w in (leaf(s.text) or "").split() if w not in SEG_CONNECT)
        # Traversal-only aggregation spans already lose all connector text.
        # Discard only this reviewed neutral frame, never symbolic operators.
        if explicit_aggregation and not t and s.type == "aggregation" and re.fullmatch(r"(?:across|over)(?:\s+(?:the|a|an))?", s.text.strip(), re.I):
            continue
        typ = "period" if s.type in ("period", "modifier") and STEP_PHRASE.match(t) else s.type  # Canonicalize equivalent period/modifier representations.
        if out and out[-1][0] == typ:
            out[-1] = (typ, (out[-1][1] + " " + t).strip())
        else:
            out.append((typ, t))
    return out


def canonical(a: Annotation):
    return {"tree": canon_tree(a.tree), "segments": canon_segments(a)}
