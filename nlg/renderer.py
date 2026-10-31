"""Recursive grammatical compilation, independent of domain/result combinations.

Only semantic primitives select constructions. Domains supply noun lexemes,
units, entities and group membership, never full sentence templates.
"""
from decimal import Decimal
import json
from time import perf_counter_ns

from pydantic import ValidationError
from simplenlg import Lexicon, NLGFactory, Realiser
from simplenlg.features import Feature, Form, NumberAgreement, Tense
from simplenlg.framework import StringElement

from .schema import (Aggregate, And, Arithmetic, Domain, Fact, Not, Property,
                     Quantity, Relation, Result, Series, Text, bounded)
from .semantics import Evaluator, check

# Lexical knowledge for a finite algebra, shared by all domains/compositions.
AGGREGATIONS = {"sum": "sum", "average": "average", "minimum": "minimum", "maximum": "maximum"}
ARITHMETIC = {"add": ("add", "to"), "subtract": ("subtract", "from"),
              "multiply": ("multiply", "by"), "divide": ("divide", "by")}
COMPARISONS = {"gt": "greater", "lt": "less"}


class CompositionalRealiser:
    def __init__(self):
        lexicon = Lexicon.getDefaultLexicon()
        self.factory = NLGFactory(lexicon)
        self.realiser = Realiser(lexicon)

    def noun(self, lexeme, *, determiner=None, plural=None):
        noun = self.factory.createNounPhrase(lexeme.head)
        if lexeme.modifiers:
            # The schema supplies an ordered compound term. Separate adjective
            # elements can acquire commas (e.g. "first, release year").
            noun.addPreModifier(StringElement(" ".join(lexeme.modifiers)))
        is_plural = lexeme.number == "plural" if plural is None else plural
        noun.setFeature(Feature.NUMBER, NumberAgreement.PLURAL if is_plural else NumberAgreement.SINGULAR)
        if determiner:
            noun.setSpecifier(determiner)
        return noun

    def pp(self, preposition, complement):
        return self.factory.createPrepositionPhrase(preposition, complement)

    def coordinate(self, elements):
        # pySimpleNLG's factory accepts at most two positional elements;
        # addCoordinate supports lists of any permitted size.
        phrase = self.factory.createCoordinatedPhrase()
        for element in elements:
            phrase.addCoordinate(element)
        return phrase

    def metric_phrase(self, domain, metric, qualifiers, plural=None):
        definition = domain.metrics[metric]
        phrase = self.noun(definition.noun, plural=plural)
        for key in qualifiers:
            phrase.addPreModifier(StringElement(definition.qualifiers[key]))
        return phrase

    def property_phrase(self, domain, expr):
        noun = self.metric_phrase(domain, expr.metric, expr.qualifiers)
        noun.setSpecifier("the")
        # A shared "of" construction also handles multiword proper names;
        # the installed library drops possessive marking on such names.
        noun.addPostModifier(self.pp("of", StringElement(domain.entities[expr.entity])))
        if expr.scope:
            scope = domain.scopes[expr.scope]
            noun.addPostModifier(self.pp(scope.preposition, StringElement(scope.label)))
        return noun

    def expression(self, domain, expr):
        if isinstance(expr, Property):
            return self.property_phrase(domain, expr)
        if isinstance(expr, Series):
            group = domain.groups[expr.group]
            if group.axis == "scope":
                noun = self.property_phrase(domain, Property(node="Property", entity=expr.entity,
                                            metric=expr.metric, qualifiers=expr.qualifiers))
                members = self.noun(group.noun, determiner=f"all {len(group.members)}", plural=True)
                if group.context:
                    members.addPostModifier(self.pp("of", StringElement(domain.scopes[group.context].label)))
                else:
                    labels = self.coordinate([StringElement(domain.scopes[k].label) for k in group.members])
                    members.addPostModifier(self.pp("of", labels))
                noun.addPostModifier(self.pp("over", members))
            else:
                noun = self.metric_phrase(domain, expr.metric, expr.qualifiers, plural=len(group.members) > 1)
                noun.setSpecifier("the")
                owners = self.coordinate([StringElement(domain.entities[k]) for k in group.members])
                noun.addPostModifier(self.pp("of", owners))
                if group.context:
                    scope = domain.scopes[group.context]
                    noun.addPostModifier(self.pp(scope.preposition, StringElement(scope.label)))
            return noun
        if isinstance(expr, Aggregate):
            noun = self.factory.createNounPhrase("the", AGGREGATIONS[expr.fn])
            noun.addPostModifier(self.pp("of", self.expression(domain, expr.over)))
            return noun
        if isinstance(expr, Arithmetic):
            verb, preposition = ARITHMETIC[expr.op]
            first, second = (expr.right, expr.left) if expr.op == "subtract" else (expr.left, expr.right)
            action = self.factory.createClause(None, verb, self.expression(domain, first))
            action.setFeature(Feature.FORM, Form.GERUND)
            action.addComplement(self.pp(preposition, self.expression(domain, second)))
            noun = self.factory.createNounPhrase("the", "result")
            noun.addPostModifier(self.pp("of", action))
            return noun
        if isinstance(expr, Quantity):
            unit = domain.units[expr.unit]
            value = Decimal(expr.value)
            digits = format(value.copy_abs(), ",f" if unit.grouping else "f")
            if "." in digits:
                digits = digits.rstrip("0").rstrip(".")
            return StringElement(("-" if value < 0 else "") + unit.prefix + digits + unit.suffix)
        if isinstance(expr, Text):
            return StringElement(json.dumps(expr.value, ensure_ascii=False))
        raise ValueError("unsupported expression")

    def statement(self, domain, stmt, invert=False):
        if isinstance(stmt, Not):
            return self.statement(domain, stmt.expr, not invert)
        if isinstance(stmt, And):
            clauses = self.coordinate([self.statement(domain, x) for x in stmt.items])
            if not invert:
                return clauses
            # Preserve negation of the entire conjunction, rather than applying
            # negation to each child (which would change its truth conditions).
            outer = self.factory.createClause("it", "be", "the case")
            outer.setFeature(Feature.TENSE, Tense.PAST)
            outer.setFeature(Feature.NEGATED, True)
            outer.addComplement(clauses)
            return outer
        if isinstance(stmt, Relation):
            clause = self.factory.createClause(self.expression(domain, stmt.left), "be")
            right = self.expression(domain, stmt.right)
            if stmt.op == "eq":
                clause.setObject(right)
            else:
                # The lexicon normalises greater to great; mark that adjective
                # comparative. "less" remains a fixed irregular lexical item.
                adjective = self.factory.createAdjectivePhrase(StringElement(COMPARISONS[stmt.op]))
                adjective.setFeature(Feature.IS_COMPARATIVE, stmt.op == "gt")
                adjective.addComplement(self.pp("than", right))
                clause.addComplement(adjective)
            clause.setFeature(Feature.TENSE, Tense.PAST)
            clause.setFeature(Feature.NEGATED, invert)
            return clause
        raise ValueError("unsupported statement")

    def render(self, bundle):
        started = perf_counter_ns()
        try:
            check(isinstance(bundle, dict) and set(bundle) == {"domain", "facts", "input"}, "invalid bundle fields")
            check(bounded(bundle["input"]) <= 2048, "meaning tree exceeds node limit")
            domain = Domain.model_validate(bundle["domain"])
            facts = [Fact.model_validate(f) for f in bundle["facts"]]
            result = Result.model_validate(bundle["input"])
            evaluator = Evaluator(domain, facts)
            truth = evaluator.statement(result.statement)
            check(truth == result.result, "asserted result disagrees with evidence")
            grammatical = self.statement(domain, result.statement, invert=not truth)
            answer = self.realiser.realiseSentence(grammatical)
            ids = {i for step in evaluator.trace for i in step.get("evidence", [])}
            return {"outcome": "answered", "answer": answer, "result": truth,
                    "meaning": result.model_dump(), "trace": evaluator.trace,
                    "evidence": [f.model_dump() for f in facts if f.id in ids],
                    "provenance": domain.provenance, "nlg_ms": (perf_counter_ns() - started) / 1e6}
        except (ValueError, ValidationError, TypeError) as exc:
            return {"outcome": "abstained", "answer": None, "reason": str(exc), "evidence": [],
                    "nlg_ms": (perf_counter_ns() - started) / 1e6}
