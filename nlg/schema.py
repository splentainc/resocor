"""The finite meaning language. All domain vocabulary is input data."""
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Noun(Strict):
    head: str = Field(min_length=1)
    modifiers: list[str] = Field(default_factory=list)
    number: Literal["singular", "plural"] = "singular"


class Unit(Strict):
    dimension: dict[str, int]
    prefix: str = ""
    suffix: str = ""
    grouping: bool = True


class Metric(Strict):
    noun: Noun
    kind: Literal["number", "text"]
    unit: str | None = None
    summable: bool = True
    qualifiers: dict[str, str] = Field(default_factory=dict)


class Scope(Strict):
    label: str = Field(min_length=1)
    preposition: Literal["in", "at", "on", "during"] = "in"


class Group(Strict):
    axis: Literal["scope", "entity"]
    members: list[str] = Field(min_length=1, max_length=32)
    noun: Noun
    context: str | None = None

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.members)) != len(self.members):
            raise ValueError("group members must be unique")
        return self


class Domain(Strict):
    name: str
    provenance: str
    entities: dict[str, str]
    metrics: dict[str, Metric]
    units: dict[str, Unit]
    scopes: dict[str, Scope] = Field(default_factory=dict)
    groups: dict[str, Group] = Field(default_factory=dict)


class Fact(Strict):
    id: str = Field(min_length=1)
    entity: str
    metric: str
    scope: str | None = None
    qualifiers: list[str] = Field(default_factory=list)
    value: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_record: dict = Field(default_factory=dict)


class Property(Strict):
    node: Literal["Property"]
    entity: str
    metric: str
    scope: str | None = None
    qualifiers: list[str] = Field(default_factory=list)


class Series(Strict):
    node: Literal["Series"]
    entity: str | None = None
    metric: str
    group: str
    qualifiers: list[str] = Field(default_factory=list)


class Quantity(Strict):
    node: Literal["Quantity"]
    value: str
    unit: str


class Text(Strict):
    node: Literal["Text"]
    value: str = Field(min_length=1)


class Aggregate(Strict):
    node: Literal["Aggregate"]
    fn: Literal["sum", "average", "minimum", "maximum"]
    over: Series


class Arithmetic(Strict):
    node: Literal["Arithmetic"]
    op: Literal["add", "subtract", "multiply", "divide"]
    left: "Expression"
    right: "Expression"


Expression = Annotated[Union[Property, Series, Quantity, Text, Aggregate, Arithmetic], Field(discriminator="node")]
Arithmetic.model_rebuild()


class Relation(Strict):
    node: Literal["Relation"]
    op: Literal["eq", "gt", "lt"] = "eq"
    left: Expression
    right: Expression


class Not(Strict):
    node: Literal["Not"]
    expr: "Statement"


class And(Strict):
    node: Literal["And"]
    items: list["Statement"] = Field(min_length=2, max_length=8)


Statement = Annotated[Union[Relation, Not, And], Field(discriminator="node")]
Not.model_rebuild()
And.model_rebuild()


class Result(Strict):
    version: Literal[2] = 2
    statement: Statement
    result: bool


def bounded(tree, depth=0):
    """Reject large inputs before recursive Pydantic union parsing."""
    if depth > 24:
        raise ValueError("meaning tree exceeds depth limit")
    if isinstance(tree, dict):
        if len(tree) > 16:
            raise ValueError("meaning node exceeds field limit")
        return 1 + sum(bounded(v, depth + 1) for v in tree.values())
    if isinstance(tree, list):
        if len(tree) > 32:
            raise ValueError("meaning node exceeds list limit")
        return 1 + sum(bounded(v, depth + 1) for v in tree)
    return 1
