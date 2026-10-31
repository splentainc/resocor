"""Unify fiscal scope and entity references from typed spans, preserving evidence."""


def bind_entities(rules, items, question):
    entities = [it for it in items if rules.view(it)['phrase_type'] == 'entity']
    if len(entities) != 1:
        return items
    entity = entities[0]
    word = lambda it: question[it['ch0']:it['ch1']].casefold()
    for n, it in enumerate(items):
        definite = (word(it) in {'company', 'firm', 'business'} and n and word(items[n - 1]) == 'the')
        if definite and rules.view(it)['phrase_type'] in {'grammar', 'unlisted'}:
            reference = {'kind': 'entity', 'source_span': [items[n - 1]['ch0'], it['ch1']],
                         'anchor_span': [entity['ch0'], entity['ch1']],
                         'entity': rules.view(entity)['valid_phrase']}
            for part in items[n - 1:n + 1]:
                part.update(ptype='grammar', consumed=True, seg_type='filler')
            it['scope_binding'] = reference
    return items


def bind_periods(rules, items, question):
    typ = lambda n: rules.view(items[n])['phrase_type'] if 0 <= n < len(items) else None
    val = lambda n: rules.view(items[n])['valid_phrase'] if 0 <= n < len(items) else None
    word = lambda n: question[items[n]['ch0']:items[n]['ch1']].casefold() if 0 <= n < len(items) else ''
    entities = [it for it in items if rules.view(it)['phrase_type'] == 'entity']
    years = [n for n in range(len(items)) if typ(n) in {'period_fy', 'period_year'}]
    # Scope must be unique before references can be unified. Other existing
    # constructions retain their own multi-entity/range handling.
    if len(entities) != 1 or len(years) != 1 or any(typ(n) == 'quarter_set' for n in range(len(items))):
        return items
    if any(typ(n) in {'negation', 'quantifier', 'forecast', 'hypothetical', 'range_marker', 'bound'}
           or word(n) in {'calendar', 'versus', 'vs'} for n in range(len(items))):
        return items
    year_index = years[0]
    year = rules._year_of(word(year_index))
    if not year:
        return items
    frames = []
    for k in range(len(items)):
        j, quarters, selection = k, None, None
        first = typ(j) == 'ordinal' and val(j) == '1'
        all_ = typ(j) == 'coverage' and word(j) == 'all'
        if first or all_:
            j += 1
            if all_ and word(j) == 'the':
                j += 1
            if typ(j) in {'number_word', 'number'}:
                try:
                    count = int(val(j) or word(j))
                except (ValueError, TypeError):
                    count = 0
                if count in (1, 2, 3, 4) and (not all_ or count == 4):
                    quarters, selection = list(range(1, count + 1)), 'all' if all_ else 'first'
                    j += 1
        if quarters:
            if word(j) == 'fiscal':
                j += 1
            if typ(j) == 'step' and val(j) == 'quarter':
                frames.append((k, j + 1, quarters, selection))
            continue
        if typ(k) == 'ordinal':
            j = k + 1
            if word(j) == 'fiscal':
                j += 1
            if typ(j) == 'period_unit' and val(j) == 'quarter':
                frames.append((k, j + 1, [int(val(k))], None))
        elif typ(k) == 'period_quarter':
            import re
            m = re.fullmatch(r'q([1-4])', word(k))
            if m:
                frames.append((k, k + 1, [int(m[1])], None))
    if len(frames) != 1:
        return items
    start, end, quarters, selection = frames[0]
    # Preserve the original parser's already-supported adjacent period forms.
    next_ = end + (word(end) in {'of', 'in'})
    if not selection and (year_index == end or year_index == next_):
        return items
    # Fronted year scope must be explicitly delimited. A following year can be
    # connected through a possessive or reference, never through another clause.
    if year_index < start:
        if not any(word(n) in {',', ':'} for n in range(year_index + 1, start)):
            return items
    else:
        allowed = {'of', 'in', 'its', 'the', "'s", '’s', 'fiscal', 'year'}
        if any(typ(n) != 'entity' and not items[n].get('scope_binding') and word(n) not in allowed
               for n in range(end, year_index)):
            return items
    if not any(word(n) == 'fiscal' for n in range(start, end)) and typ(year_index) != 'period_fy':
        return items
    y, entity = items[year_index], entities[0]
    binding = {'kind': 'fiscal_period', 'source_span': [items[start]['ch0'], items[end - 1]['ch1']],
               'anchor_span': [y['ch0'], y['ch1']], 'fiscal_year': year, 'quarters': quarters,
               'entity': rules.view(entity)['valid_phrase']}
    if selection:
        leaf = rules._span(items[start:end], 'quarter_set',
            quarter_set={'fiscal_year': year, 'quarters': quarters, 'selection': selection},
            year_ch=(y['ch0'], y['ch1']), step_text=word(end - 1),
            coverage_text='all 4' if selection == 'all' else None, scope_binding=binding)
    else:
        leaf = rules._span(items[start:end], 'period_phrase',
                           resolved_period=f'Q{quarters[0]} FY{year}', scope_binding=binding)
    y.update(ptype='grammar', seg_type='period', consumed=True)
    out = items[:start] + [leaf] + items[end:]
    # The existing connector rule now sees the resolved set.
    from quarter_composition import compose_quarters
    return compose_quarters(rules, out, question)
