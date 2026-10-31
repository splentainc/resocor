"""Finite fiscal quarter collections composed from typed lexical leaves."""

def compose_quarters(rules, items, question):
    t = lambda n: rules.view(items[n])['phrase_type'] if 0 <= n < len(items) else None
    w = lambda n: question[items[n]['ch0']:items[n]['ch1']].casefold() if n < len(items) else ''
    v = lambda n: rules.view(items[n])['valid_phrase'] if n < len(items) else None
    out, k = [], 0
    while k < len(items):
        first = t(k) == 'ordinal' and v(k) == '1'
        all_ = t(k) == 'coverage' and w(k) == 'all'
        j = k + 1
        if all_ and w(j) == 'the':
            j += 1
        count = None
        if (first or all_) and t(j) in {'number_word', 'number'}:
            try:
                count = int(v(j) or w(j))
            except (ValueError, TypeError):
                pass
            j += 1
        fiscal = w(j) == 'fiscal' and t(j) == 'period_marker'
        if fiscal:
            j += 1
        if count in (1, 2, 3, 4) and (not all_ or count == 4) and t(j) == 'step' and v(j) == 'quarter':
            unit = j
            j += 1
            if w(j) in {'of', 'in'} and t(j) == 'grammar':
                j += 1
            if w(j) == 'fiscal' and t(j) == 'period_marker':
                fiscal = True
                j += 1
            if t(j) in {'period_fy', 'period_year'} and (fiscal or t(j) == 'period_fy'):
                year = rules._year_of(w(j))
                if year:
                    out.append(rules._span(items[k:j + 1], 'quarter_set',
                        quarter_set={'fiscal_year': year, 'quarters': list(range(1, count + 1)),
                                     'selection': 'all' if all_ else 'first'},
                        year_ch=(items[j]['ch0'], items[j]['ch1']),
                        step_text=w(unit), coverage_text='all 4' if all_ else None))
                    k = j + 1
                    continue
        out.append(items[k])
        k += 1
    # Across a set is traversal syntax; an explicit sum/average still owns its operation.
    for n, it in enumerate(out):
        if rules.view(it)['phrase_type'] in {'aggregation', 'grammar'} and question[it['ch0']:it['ch1']].casefold() in {'across', 'over'}:
            tail = n + 1
            if tail < len(out) and question[out[tail]['ch0']:out[tail]['ch1']].casefold() == 'the':
                tail += 1
            if tail < len(out) and rules.view(out[tail])['phrase_type'] == 'quarter_set':
                it.update(ptype='grammar', seg_type='filler', consumed=True)
    return out
