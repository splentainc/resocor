# RESOCOR

**Evidence-grounded answers, explicit decisions, and abstention when support is missing.**

RESOCOR is an experimental Python project exploring how much useful reasoning
can be done with explicit facts, checked operations and authored rules. Its
current engine uses a deterministic question parser and runs without an LLM at
question time. Facts and domain vocabulary are supplied separately from code.

The aim is to make answers inspectable: which question was understood, which
records were selected, which calculation or rule ran, and why an answer was
given or withheld.

## How it works

```text
Question + supplied domain vocabulary
                  |
          Typed interpretation
                  |
       Checked lookup / calculation <--- Supplied facts and fiscal calendar
                  |
       Source and consistency checks
                  |
       Optional authored decision table
                  |
       Deterministic English rendering

Unsupported interpretation, missing evidence or failed checks -> abstention
```

| Module | Purpose |
| --- | --- |
| `engine/` | Domain phrase lookup, typed question trees, checked execution, evidence receipts and a local conversation workbench |
| `nlg/` | Validate supplied meaning/fact bundles and render them through pySimpleNLG in crisp, detailed or audit form |
| `dmn/` | Evaluate a documented subset of FEEL and typed JSON decision tables in-process |
| `abem/` | Experimental construction, verification and bounded execution of diagnostic/intervention programs |
| `chatter/` | Match a separately supplied social-phrase dataset, with domain meaning taking precedence |

These modules have different maturity and scope. Their presence does not imply
a complete general reasoning system or full DMN/FEEL standards conformance.

## Run the public example

Python 3.12 or newer is required. From the source directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r nlg/requirements.lock.txt
.venv/bin/python -m examples.minimal.demo
.venv/bin/python -m examples.minimal.demo --detail audit
.venv/bin/python -m examples.minimal.demo --year 2025
```

The example contains **one fictional company, one invented revenue record and
one fiscal period**. It calls the existing fact selector, evaluates a numeric
comparison with FEEL, and asks NLG to validate and express the result. The 2025
request abstains because no corresponding record is supplied.

This is a structured-input demonstration of reusable components. It does not
exercise natural-language parsing, reporting-context checks or the full
`engine.answer` pipeline, and its values are not financial information.

Run the tests included in the public distribution:

```sh
.venv/bin/python -m unittest examples.minimal.test_demo
.venv/bin/python -m unittest discover -s nlg/tests -t .
.venv/bin/python -m unittest discover -s dmn/tests -t .
```

## Runtime data

The supplied example uses synthetic data. Applications supply their own domain
vocabulary, facts and supporting metadata.

The natural-language engine and its web workbench require a separately supplied,
compatible domain pack: glossary, facts, fiscal calendar, domain metadata,
reporting context and predicate contracts, plus the shared chatter dataset.
They cannot start from the public example alone. The current finance adapter
still has domain-specific assumptions; arbitrary domain packs are not a
supported plug-and-play interface.

See [data interfaces](docs/data-interfaces.md) for the expected input structures
and current runtime paths.

## What the checks establish

Within supported operations, factual values come from supplied records and
calculations use explicit operands. Missing, conflicting or unsupported inputs
can produce an abstention rather than a guessed value. NLG checks the supplied
statement against the supplied facts before expressing it.

These checks do **not** establish that an external source is true, that ingestion
is correct, that every question is interpreted correctly, or that an authored
rule is appropriate. A wrong interpretation can select the wrong true fact.
Source authenticity, completeness, freshness and business-policy approval need
their own controls.

The project remains bounded research software. Passing tests do not certify
general reliability, production readiness or arbitrary natural-language coverage.

## License and attribution

RESOCOR's original source code, documentation and authored synthetic examples
are licensed under the [Apache License, Version 2.0](LICENSE), except where
separate terms are explicitly identified. See [NOTICE](NOTICE) for attribution.

Third-party dependencies and separately supplied domain knowledge or datasets
retain their own terms. The synthetic examples do not establish rights to
redistribute separately obtained datasets.
