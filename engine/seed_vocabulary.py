"""Typed corpus seed roles, applied only through explicit construction contexts."""
if __package__:
    from .tokenizer import tokenize
else:
    from tokenizer import tokenize

ROLES = {'request_head','request_desire','reporting_flow','sum_collection','avg_collection','metric_wrapper'}


def lexical_frame(row):
    """Request/wrapper candidates carry no independent fact or operation.

    A longer reviewed syntax frame may account for them. Reporting, arithmetic
    and all other semantic roles must still veto overlapping grammar spans.
    """
    roles = {spec['role'] for spec in row.get('seed_roles', [])}
    return row.get('phrase_type') == 'seed_construction' and bool(roles) and roles <= {
        'request_head', 'request_desire', 'metric_wrapper'}


def attach(connection, rows):
    if not connection.execute("select 1 from sqlite_master where type='table' and name='seed_constructions'").fetchone():
        return rows
    import json
    by = {}
    for surface, role, encoded in connection.execute('select surface,role,spec from seed_constructions order by surface,role'):
        if role not in ROLES:
            raise ValueError('unknown imported construction role')
        spec=json.loads(encoded)
        if spec['role']!=role or spec['phrase']!=surface:
            raise ValueError('invalid corpus construction record')
        by.setdefault(surface,[]).append(spec)
    found=set()
    for row in rows:
        if row['user_phrase'].casefold() in by:
            row['seed_roles']=by[row['user_phrase'].casefold()]
            found.add(row['user_phrase'].casefold())
    for surface in sorted(set(by)-found):
        # Historical segment observations have a unique surface index but no
        # lexical meaning. Independent corpus review supplies meaning in the
        # construction table; retain the original observation bytes untouched.
        rows.append({'user_phrase':surface,'valid_phrase':'seed:'+by[surface][0]['role'],
                     'phrase_type':'seed_construction','match':'phrase','source':'reviewed corpus construction table',
                     'reviewed':1,'seed_roles':by[surface]})
    return rows


def specs(gloss, role):
    seen=set()
    for row in gloss:
        for spec in row.get('seed_roles',[]):
            key=(spec['phrase'],spec['role'])
            if spec['role']==role and key not in seen:
                seen.add(key);yield spec


def matches(question, items, spec):
    words=tuple(t.casefold() for t,_,_ in tokenize(spec['phrase']))
    tokens=tokenize(question)
    for k in range(len(tokens)-len(words)+1):
        if tuple(t.casefold() for t,_,_ in tokens[k:k+len(words)])!=words:
            continue
        lo,hi=tokens[k][1],tokens[k+len(words)-1][2]
        indexes=[n for n,it in enumerate(items) if it['ch0']>=lo and it['ch1']<=hi]
        if indexes and items[indexes[0]]['ch0']==lo and items[indexes[-1]]['ch1']==hi:
            yield indexes


def license_seed_requests(question, items, view, gloss):
    types=[view(it)['phrase_type'] for it in items]
    if types.count('entity')!=1 or types.count('metric')!=1:return
    metric=types.index('metric')
    artifact_head = (metric+1<len(items) and question[items[metric+1]['ch0']:items[metric+1]['ch1']].casefold() in {'report','chart','graph','document','forecast','projection'})
    starts={0}|{n+1 for n,it in enumerate(items[:-1]) if question[it['ch0']:it['ch1']]==','}
    def word(n):return question[items[n]['ch0']:items[n]['ch1']].casefold()
    for role in ('request_head','request_desire'):
        for spec in specs(gloss,role):
            for indexes in matches(question,items,spec):
                start=indexes[0]
                licensed=any(all(word(n) in {'please','can','could','would','you'} for n in range(a,start)) for a in starts if a<=start)
                if artifact_head or not licensed or any(types[n] not in {'grammar','unlisted','hypothetical_aux','seed_construction'} for n in indexes):continue
                for n in indexes:
                    items[n].update(ptype='grammar',seg_type='filler',consumed=True,construction_role=role,seed_mapping=spec['mapping_id'])
                end=indexes[-1]+1
                if end<len(items) and word(end) in {'me','us'} and types[end] in {'grammar','unlisted'}:
                    items[end].update(ptype='grammar',seg_type='filler',consumed=True,construction_role='request_recipient')
    metric=types.index('metric')
    for spec in specs(gloss,'metric_wrapper'):
        for indexes in matches(question,items,spec):
            adjacent=indexes[-1]+1==metric or metric+1==indexes[0]
            if adjacent and all(types[n] in {'grammar','unlisted','modifier_tail','seed_construction'} for n in indexes):
                for n in indexes:items[n].update(ptype='grammar',seg_type='filler',consumed=True,construction_role='metric_wrapper',seed_mapping=spec['mapping_id'])
    if types.count('quarter_set')==1 and not any(t in {'compare_op','quantity','negation'} for t in types):
        for role,fn in [('sum_collection','sum'),('avg_collection','avg')]:
            for spec in specs(gloss,role):
                for indexes in matches(question,items,spec):
                    # The lexical phrase must remain one span. Never splice a
                    # typed entity/metric or a separate operation into filler.
                    if len(indexes)==1 and types[indexes[0]] in {'grammar','unlisted','aggregation','arithmetic','seed_construction'}:
                        items[indexes[0]].update(ptype='aggregation',valid=fn,seg_type='aggregation',seed_mapping=spec['mapping_id'])


def reporting_forms(gloss):
    return {s['phrase']:s for s in specs(gloss,'reporting_flow')}
