"""Check operator/metric ambiguity using parser alternatives, before fact lookup."""
from annotation_schema import validate


def check_competing(question, qid, primary, parse, lookup, view, gloss, phrases):
    if primary.tree.node == 'Unsupported':
        return primary
    _, items = lookup(question)
    # An explicit enclosing operator takes a complete metric argument.
    if any(view(it)['phrase_type'] == 'aggregation' for it in items):
        return primary
    alternatives = []
    for it in items:
        if view(it)['phrase_type'] != 'metric' or it['row'] is None:
            continue
        row = gloss[it['row']]
        if row.get('reviewed') != 1:
            continue
        from tokenizer import tokenize
        words = tuple(t.casefold() for t, _, _ in tokenize(question[it['ch0']:it['ch1']]))
        for cut in range(1, len(words)):
            op, tail = phrases.get(words[:cut]), phrases.get(words[cut:])
            if op is None or tail is None:
                continue
            if (gloss[op]['phrase_type'] == 'aggregation' and gloss[tail]['phrase_type'] == 'metric'
                    and gloss[tail]['valid_phrase'] == row['valid_phrase']):
                alternatives.append((it['start'], it['start'] + cut, op))
    if len(alternatives) <= 4:
        for split in alternatives:
            other = parse(question, qid, split)
            # A total amount and an unaggregated collection differ in meaning.
            if other.tree.node != 'Unsupported' and other.tree.node != primary.tree.node:
                break
        else:
            return primary
    data = primary.model_dump(by_alias=True, exclude_none=True)
    data['tree'] = {'node': 'Unsupported', 'reason': 'competing complete-metric and aggregation interpretations'}
    return validate(data, question)
