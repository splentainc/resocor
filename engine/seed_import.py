"""Atomic import of reviewed fact mappings and explicitly typed corpus constructions."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
from .lexicon_import import read_bundle, digest, ROOT
from .seed_vocabulary import ROLES


COMPATIBLE={
 'request_head':{'grammar'},'request_desire':{'grammar','hypothetical_aux'},
 'reporting_flow':{'grammar'},'metric_wrapper':{'grammar','modifier_tail'},
 'sum_collection':{'grammar','aggregation','arithmetic'},'avg_collection':{'grammar','aggregation'},
}

for _allowed in COMPATIBLE.values():
    _allowed.add('seed_construction')


def validate_pack(path,root=ROOT):
    pack=json.loads(Path(path).read_text())
    if pack.get('schema_version')!=1 or pack.get('kind')!='reviewed_corpus_seed':
        raise ValueError('unsupported seed pack')
    facts=root/pack['fact_bundle']
    if digest(facts)!=pack['fact_bundle_sha256']:
        raise ValueError('fact bundle changed')
    read_bundle(facts,root)
    for row in pack['constructions']:
        if row['role'] not in ROLES or row.get('reviewed') is not True or not row.get('provenance'):
            raise ValueError('unreviewed or unknown construction role')
        if row['phrase']!=row['phrase'].strip().casefold() or not row.get('mapping_id'):
            raise ValueError('invalid construction surface')
        if row['constraints']!={'entities':1,'metrics':1,'unknown_content':'retain','context':'typed_'+row['role']}:
            raise ValueError('construction guard differs from reviewed capability')
    if not pack.get('source_receipts') or not pack.get('review_sha256'):
        raise ValueError('missing corpus/review provenance')
    for row in pack.get('operators',[]):
        if (row['kind']!='compare_op' or row['value'] not in {'gt','gte','lt','lte','eq'}
                or row.get('reviewed') is not True or not row.get('provenance')):
            raise ValueError('unreviewed operator mapping')
    return pack,json.loads(facts.read_text())


def import_pack(path,glossary,*,root=ROOT,apply=False):
    pack,facts=validate_pack(path,root);pin=digest(path)
    stats={'facts_added':0,'constructions_added':0,'construction_lexical_rows_added':0,'virtual_grammar_surfaces':0,'existing_constructions':0,'applied':apply,'pack_sha256':pin}
    con=sqlite3.connect(glossary)
    try:
        con.execute('begin immediate')
        for row in facts['entries']:
            old=con.execute("select phrase_type,valid_phrase from glossary where lower(user_phrase)=? and phrase_type not like 'segment:%'",(row['phrase'].casefold(),)).fetchall()
            equivalent=lambda v:facts['entity_equivalence'].get(v,v) if row['kind']=='entity' else v
            if any(k!=row['kind'] or equivalent(v)!=equivalent(row['value']) for k,v in old):
                raise ValueError('fact lexical conflict: '+row['phrase'])
            if not old:
                con.execute('insert into glossary(user_phrase,valid_phrase,phrase_type,match,source,reviewed) values(?,?,?,?,?,1)',
                    (row['phrase'],row['value'],row['kind'],'phrase','reviewed corpus facts:'+pin+'; '+row['provenance']))
                stats['facts_added']+=1
        stats['operators_added']=0
        for row in pack.get('operators',[]):
            old=con.execute("select phrase_type,valid_phrase from glossary where lower(user_phrase)=? and phrase_type not like 'segment:%'",(row['phrase'],)).fetchall()
            if any(k!=row['kind'] or v!=row['value'] for k,v in old):
                raise ValueError('operator meaning conflict')
            if not old:
                con.execute('insert into glossary(user_phrase,valid_phrase,phrase_type,match,source,reviewed) values(?,?,?,?,?,1)',
                    (row['phrase'],row['value'],row['kind'],'phrase','reviewed corpus operator:'+pin))
                stats['operators_added']+=1
        con.execute('create table if not exists seed_constructions(surface text,role text,spec text,pack_sha256 text,primary key(surface,role))')
        for row in pack['constructions']:
            old=con.execute("select phrase_type,valid_phrase from glossary where lower(user_phrase)=? and phrase_type not like 'segment:%'",(row['phrase'],)).fetchall()
            if any(k not in COMPATIBLE[row['role']] for k,_ in old):
                raise ValueError('construction lexical conflict: '+row['phrase'])
            if row['role']=='sum_collection' and any(k in {'aggregation','arithmetic'} and v not in {'add','sum'} for k,v in old):
                raise ValueError('sum operator conflict')
            if row['role']=='avg_collection' and any(k=='aggregation' and v!='avg' for k,v in old):
                raise ValueError('average operator conflict')
            encoded=json.dumps(row,sort_keys=True)
            prior=con.execute('select spec from seed_constructions where surface=? and role=?',(row['phrase'],row['role'])).fetchone()
            if prior and prior[0]!=encoded:
                raise ValueError('conflicting construction review')
            if not prior:
                con.execute('insert into seed_constructions values(?,?,?,?)',(row['phrase'],row['role'],encoded,pin));stats['constructions_added']+=1
            else:stats['existing_constructions']+=1
            observation=con.execute('select 1 from glossary where lower(user_phrase)=?',(row['phrase'],)).fetchone()
            if not old and observation:
                stats['virtual_grammar_surfaces']+=1
            # Upgrade only rows owned by this still-current reviewed pack.
            # Existing inherited grammar/domain rows retain their original type.
            con.execute("update glossary set phrase_type='seed_construction' where lower(user_phrase)=? and source=? and phrase_type='grammar'",
                        (row['phrase'],'reviewed corpus construction:'+pin))
            if not old and not observation:
                con.execute('insert into glossary(user_phrase,valid_phrase,phrase_type,match,source,reviewed) values(?,?,?,?,?,1)',
                    (row['phrase'],'seed:'+row['role'],'seed_construction','phrase','reviewed corpus construction:'+pin))
                stats['construction_lexical_rows_added']+=1
        con.execute('create table if not exists seed_pack_imports(pack_sha256 text primary key,manifest text not null)')
        con.execute('insert or ignore into seed_pack_imports values(?,?)',(pin,json.dumps(pack,sort_keys=True)))
        con.commit() if apply else con.rollback()
        return stats
    except Exception:
        con.rollback();raise
    finally:con.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('pack',type=Path);p.add_argument('--glossary',type=Path,default=ROOT/'engine/knowledge/glossary.db');p.add_argument('--apply',action='store_true')
    a=p.parse_args();print(json.dumps(import_pack(a.pack,a.glossary,apply=a.apply),indent=2))
