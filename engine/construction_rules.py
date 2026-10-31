"""Shared typed constructions. Lexical triggers never become global filler."""


def compose_comparators(rules, items, question):
    out, k = [], 0
    word = lambda n: question[items[n]['ch0']:items[n]['ch1']].casefold()
    while k < len(items):
        a = rules.view(items[k])
        j = k + 1
        if a['phrase_type'] == 'compare_op' and a['valid_phrase'] == 'eq' and j < len(items) and word(j) == 'to':
            j += 1
        if j + 1 < len(items) and word(j) == 'or':
            b = rules.view(items[j + 1])
            if a['phrase_type'] == b['phrase_type'] == 'compare_op':
                pair = {a['valid_phrase'], b['valid_phrase']}
                value = 'gte' if pair == {'eq', 'gt'} else 'lte' if pair == {'eq', 'lt'} else None
                if value:
                    out.append(rules._span(items[k:j + 2], 'compare_op', valid=value))
                    k = j + 2
                    continue
        out.append(items[k]); k += 1
    return out


def license_constructions(question, items, view):
    types = [view(it)['phrase_type'] for it in items]
    if types.count('entity') != 1 or types.count('metric') != 1:
        return
    word = lambda n: question[items[n]['ch0']:items[n]['ch1']].casefold()
    metric = types.index('metric')
    def account(n, role):
        # A construction cannot overwrite a domain/operator interpretation.
        if types[n] in {'grammar', 'unlisted', 'modifier_tail'}:
            items[n].update(ptype='grammar', seg_type='filler', consumed=True, construction_role=role)
    # Request heads may follow a fronted explicit scope and a comma.
    starts = {0} | {n + 1 for n in range(len(items) - 1) if word(n) == ','}
    for start in starts:
        j = start
        while j < len(items) and word(j) in {'please', 'can', 'could', 'would', 'you'}:
            j += 1
        head = word(j) if j < len(items) else ''
        end = j + 1
        if head == 'i' and end < len(items) and word(end) in {'need', 'want'}:
            end += 1
        elif head not in {'find', 'show', 'list', 'display', 'get', 'give', 'provide', 'retrieve', 'tell', 'calculate', 'compute'}:
            continue
        for n in range(start, end):
            account(n, 'request')
        if end < len(items) and word(end) in {'me', 'us'}:
            account(end, 'request_recipient')
    for n in range(len(items)):
        if word(n) in {'amount', 'amounts', 'value', 'values', 'figure', 'figures'} and abs(n - metric) == 1:
            account(n, 'metric_wrapper')
    collection = any(t in {'quarter_set', 'step'} for t in types)
    if collection:
        for start in starts:
            if start >= len(items):
                continue
            n = start + (word(start) == 'please')
            if n < len(items) and word(n) == 'add' and types[n] in {'unlisted', 'grammar'}:
                items[n].update(ptype='aggregation', valid='sum', seg_type='aggregation')
            if n < len(items) and word(n) == 'combine' and any(t == 'aggregation' and view(it)['valid_phrase'] == 'sum' for t, it in zip(types, items)):
                account(n, 'aggregate_request')
        # Explicit trailing result phrase: into a single sum/average.
        for n in range(len(items) - 3):
            if [word(j) for j in range(n, n + 3)] == ['into', 'a', 'single'] and types[n + 3] == 'aggregation':
                for j in range(n, n + 3):
                    account(j, 'aggregate_result')
