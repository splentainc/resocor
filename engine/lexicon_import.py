"""Offline reviewed fact-lexicon import into the main glossary, never query-time generation."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_bundle(path, root=ROOT):
    bundle = json.loads(Path(path).read_text())
    return validate_bundle(bundle, root)


def validate_bundle(bundle, root=ROOT):
    """Validate file imports and the identical persisted import manifests."""
    if bundle.get('schema_version') != 1 or bundle.get('review_kind') != 'independent_machine_review':
        raise ValueError('unsupported or unreviewed fact lexicon')
    required = {'engine/data/domain.json', 'engine/data/finance_public.db', 'engine/data/reporting_basis.json'}
    if set(bundle['inputs']) != required:
        raise ValueError('fact lexicon must identify all owning metadata inputs')
    for name, pin in bundle['inputs'].items():
        if digest(root / name) != pin:
            raise ValueError('stale fact metadata: ' + name)
    domain = json.loads((root / 'engine/data/domain.json').read_text())
    aliases = bundle.get('entity_equivalence', {})
    if aliases != {}:
        raise ValueError('unreviewed entity equivalence')
    with sqlite3.connect(f'file:{root / "engine/data/finance_public.db"}?mode=ro', uri=True) as con:
        predicates = {r[0] for r in con.execute('select distinct predicate from facts')}
    for metric, contract in bundle['contracts'].items():
        if (metric not in domain['attributes'] or contract['args'] != ['Company', 'FiscalPeriod']
                or contract['returns'] != 'Money[USD]' or contract['unit'] != 'USD'
                or contract['quarter_predicate'] != metric or metric not in predicates
                or not contract['source_tags']):
            raise ValueError('invalid fact predicate contract: ' + metric)
        if contract['kind'] == 'flow':
            if contract['annual_predicate'] != metric + '_annual' or contract['annual_predicate'] not in predicates:
                raise ValueError('invalid annual flow predicate')
        elif contract['kind'] == 'stock':
            if (contract['annual_predicate'] is not None or metric + '_annual' in predicates
                    or contract.get('period_semantics') != 'at fiscal period end'):
                raise ValueError('invalid instant stock predicate')
        else:
            raise ValueError('unknown financial predicate kind')
    seen = {}
    for entry in bundle['entries']:
        if entry.get('reviewed') is not True or not entry.get('provenance'):
            raise ValueError('every imported mapping requires review and provenance')
        phrase, kind, value = entry['phrase'], entry['kind'], entry['value']
        if not phrase.strip() or phrase != phrase.strip() or len(phrase) > 160:
            raise ValueError('invalid lexical surface')
        if kind == 'metric' and value not in bundle['contracts']:
            raise ValueError('metric outside reviewed contracts')
        if kind == 'entity' and value not in domain['db_names']:
            raise ValueError('entity outside fact metadata')
        if kind == 'currency' and value != 'USD':
            raise ValueError('currency outside reviewed fact unit')
        if kind not in {'metric', 'entity', 'currency'}:
            raise ValueError('fact import cannot supply shared grammar/templates')
        key = phrase.casefold()
        if key in seen and seen[key] != (kind, value):
            raise ValueError('conflicting reviewed surfaces')
        seen[key] = kind, value
    return bundle


def import_bundle(bundle_path, glossary, *, root=ROOT, apply=False):
    bundle = read_bundle(bundle_path, root)
    pin = digest(bundle_path)
    stats = {'inserted': 0, 'existing': 0, 'contracts': len(bundle['contracts']),
             'applied': apply, 'bundle_sha256': pin, 'review_kind': bundle['review_kind']}
    con = sqlite3.connect(glossary)
    try:
        con.execute('BEGIN IMMEDIATE')
        equivalence = bundle['entity_equivalence']
        def meaning(kind, value):
            return kind, equivalence.get(value, value) if kind == 'entity' else value
        for entry in bundle['entries']:
            phrase, kind, value = entry['phrase'], entry['kind'], entry['value']
            rows = con.execute("select phrase_type,valid_phrase from glossary where lower(user_phrase)=lower(?) and phrase_type not like 'segment:%'", (phrase,)).fetchall()
            if any(meaning(k, v) != meaning(kind, value) for k, v in rows):
                raise ValueError('existing glossary meaning conflicts: ' + phrase)
            if rows:
                stats['existing'] += 1
            else:
                con.execute('insert into glossary(user_phrase,valid_phrase,phrase_type,match,source,reviewed) values(?,?,?,?,?,1)',
                    (phrase, value, kind, 'phrase', 'reviewed facts:' + pin + '; ' + entry['provenance']))
                stats['inserted'] += 1
        con.execute('create table if not exists fact_lexicon_imports(bundle_sha256 text primary key, manifest text not null)')
        old = con.execute('select manifest from fact_lexicon_imports where bundle_sha256=?', (pin,)).fetchone()
        encoded = json.dumps(bundle, sort_keys=True)
        if old and old[0] != encoded:
            raise ValueError('import provenance conflict')
        con.execute('insert or ignore into fact_lexicon_imports values(?,?)', (pin, encoded))
        con.commit() if apply else con.rollback()
        return stats
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('bundle', type=Path)
    p.add_argument('--glossary', type=Path, default=ROOT / 'engine/knowledge/glossary.db')
    p.add_argument('--apply', action='store_true')
    args = p.parse_args()
    print(json.dumps(import_bundle(args.bundle, args.glossary, apply=args.apply), indent=2))
