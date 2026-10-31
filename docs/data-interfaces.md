# Code and data interfaces

These interfaces describe the current Python components and their input data.
The supplied example uses invented data. The finance interfaces are not a stable
universal domain-pack format.

## Structured example

`examples/minimal/data.json` supplies one fictional company, one revenue record,
one fiscal year and a comparison threshold. The example constructs `Fact`
objects and uses `engine.fact_lookup.facts_for` to select a fiscal-year record.
Only one matching record permits a comparison. Zero or multiple matches abstain.

The selected value is compared using the existing FEEL evaluator. The selected
fact, explicit period, unit, statement and computed result are then passed to
NLG. Its consistency checker verifies the statement against those supplied
values; it does not establish that the source is true. The example does not
invoke the natural-language parser, finance adapter or reporting-policy checks.

## Reusable fact selection

`engine.fact_store.Fact` has these fields:

| Field | Meaning |
| --- | --- |
| `fact_id` | Caller-supplied record identity |
| `company`, `predicate` | Subject and property keys |
| `value` | Supplied value; the example uses an exact decimal string |
| `valid_from`, `valid_to` | Python dates; the end is exclusive or absent |
| `source` | Source label supplied by the caller |

`facts_for(store, calendar, case)` takes an index keyed by `(company, predicate)`
and a calendar keyed by company and fiscal year/quarter. Its `case` specifies
company, metric and a typed period. It returns records, not a certified answer.
Callers must check cardinality, source validity, units and operation semantics.
The current selector distinguishes a fixed set of stock metrics and adds an
`_annual` suffix for fiscal-year flow lookups. General domain typing is not
configurable through this function.

## NLG bundles

`nlg.NLG().render(bundle, detail="crisp")` accepts:

- `domain`: entity labels, metric nouns/types, units and optional scopes/groups.
- `facts`: record IDs, entity/metric/scope identity, exact values and provenance.
- `input`: a versioned meaning tree and the claimed result to verify.

Use `crisp`, `detailed` or `audit`. Missing/conflicting evidence, incompatible
units or a result inconsistent with the facts can abstain. The typed contracts
are defined in `nlg/schema.py`; executable generic examples are in `nlg/tests/`.

## FEEL and decision tables

`dmn.evaluate_checked(expression, variables)` evaluates supported FEEL constructs
and returns a checked value plus diagnostics. JSON decision tables have explicit
input/output types, rule IDs and a UNIQUE hit policy. `dmn/schema.py` defines
the format; `dmn/examples/` contains two illustrative tables. This implementation
supports a bounded subset and does not import standard DMN XML.

## Full natural-language finance engine

The natural-language components expect these runtime inputs:

| Current path | Required content |
| --- | --- |
| `engine/knowledge/glossary.db` | Phrase meanings/types, matching behavior, review provenance and any supported composition/construction records |
| `engine/data/finance_public.db` | `facts` records and `fiscal_periods`, with consistent subject/predicate/date identities |
| `engine/data/domain.json` | Company database names and metric metadata |
| `engine/data/reporting_basis.json` | Database-bound source/reporting context and selection metadata |
| `engine/data/fact_lexicon.json` | Validated predicate contracts, metadata hashes and reviewed mappings |
| `chatter/generic.db` | Separate versioned social-phrase dataset matching `chatter/schema.sql` |

The default engine reads these paths directly. Its existing adapter also has
finance-specific predicate/source and entity-equivalence assumptions. A pack
must satisfy those checks together; placing arbitrary records at the same paths
does not make them compatible. Metadata hashes and source contracts are
validated by `engine/lexicon_import.py` and `engine/financial_binding.py`.

The natural-language parser and web interface therefore need additional local
setup beyond the supplied structured-input example.
