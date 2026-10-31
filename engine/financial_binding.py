"""Bind native period/metric leaves under reviewed financial contracts."""
from functools import lru_cache
from pathlib import Path
import re


@lru_cache(maxsize=1)
def contracts():
    from .lexicon_import import read_bundle, validate_bundle
    import json
    import sqlite3
    root = Path(__file__).parent
    result = read_bundle(root / 'data/fact_lexicon.json')['contracts']
    with sqlite3.connect(f'file:{root / "knowledge/glossary.db"}?mode=ro', uri=True) as con:
        if con.execute("select 1 from sqlite_master where name='fact_lexicon_imports'").fetchone():
            manifests = con.execute('select manifest from fact_lexicon_imports order by bundle_sha256').fetchall()
            for encoded, in manifests:
                reviewed = validate_bundle(json.loads(encoded))['contracts']
                for metric, contract in reviewed.items():
                    keys = ('kind','unit','args','returns','quarter_predicate','annual_predicate','source_tags')
                    if metric in result and any(result[metric][k] != contract[k] for k in keys):
                        raise ValueError('conflicting imported predicate contract')
                    result[metric] = contract
    return result


def period(text, legacy):
    if not isinstance(text, str):
        raise ValueError('one explicit fiscal period is required')
    years, quarters, ordinal, units = [], [], [], 0
    for token, _, _ in legacy.tokenize(text):
        low = token.casefold()
        if re.fullmatch(r'fy\d{4}', low):
            years.append(int(low[2:])); continue
        if re.fullmatch(r'\d{4}', low):
            years.append(int(low)); continue
        if re.fullmatch(r'q[1-4]', low):
            quarters.append(int(low[1])); continue
        key = (low,)
        row = legacy.GLOSS[legacy.PHRASES[key]] if key in legacy.PHRASES else {}
        kind, value = row.get('phrase_type'), row.get('valid_phrase')
        if kind == 'ordinal':
            ordinal.append(int(value))
        elif kind == 'period_unit' and value == 'quarter':
            units += 1
        elif kind == 'period_marker' and value == 'fiscal':
            pass
        elif kind == 'grammar' and low in {'of', 'the', 'in'}:
            pass
        else:
            raise ValueError('unsupported native period leaf: ' + text)
    if ordinal:
        if len(ordinal) != 1 or units != 1:
            raise ValueError('unresolved ordinal fiscal period')
        quarters += ordinal
    if len(years) != 1 or len(quarters) > 1 or any(q not in (1, 2, 3, 4) for q in quarters):
        raise ValueError('ambiguous native fiscal period')
    out = {'kind': 'fiscal_quarter' if quarters else 'fiscal_year', 'fiscal_year': years[0]}
    if quarters:
        out['quarter'] = quarters[0]
    return out


def verify_reporting(facts, reporting, aggregate=False):
    """Validate supplied financial-source contracts for supported query shapes."""
    tags = []
    for fact in facts:
        contract = contracts()[fact['metric']]
        context = reporting['records'].get(fact['id'], {})
        selected = context.get('selected') or {}
        match = re.fullmatch(r'cached XBRL ([^:]+):.*', selected.get('measure', ''))
        original = fact['source_record']
        if (context.get('compatibility') != 'same_measure' or not match or match[1] not in contract['source_tags']
                or selected.get('unit') != 'USD' or selected.get('source') != original['source']
                or selected.get('period_start') != original['valid_from']
                or selected.get('period_end') is None):
            raise ValueError('reviewed financial source context unavailable or incompatible')
        from datetime import date, timedelta
        if contract['kind'] == 'stock':
            if selected['period_end'] != original['valid_from']:
                raise ValueError('reviewed stock source instant mismatch')
        elif not original.get('valid_to') or selected['period_end'] != (date.fromisoformat(original['valid_to']) - timedelta(days=1)).isoformat():
            raise ValueError('reviewed financial source period mismatch')
        from .structured import amount
        if amount(selected['value']) != amount(original['value']):
            raise ValueError('reviewed financial source value mismatch')
        tags.append(match[1])
    if aggregate and len(set(tags)) != 1:
        raise ValueError('aggregate source tags have no approved equivalence')
