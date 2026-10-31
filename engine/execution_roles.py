"""Check that the public executor places every native semantic role exactly once."""
import importlib


def check(question, tree):
    parser = importlib.import_module('destructure_question')
    _, items = parser.lookup_all(question)
    roles = [parser.view(it)['phrase_type'] for it in items]
    types = []
    for n, (it, role) in enumerate(zip(items, roles)):
        if role == 'grammar':
            continue
        if (role == 'number' and n and n + 1 < len(roles) and roles[n - 1] == 'coverage'
                and roles[n + 1] in {'step', 'step_modifier'}):
            continue  # the explicit coverage number is checked by resolve_checked
        types.append(role)
    kind = tree['node']
    if kind not in {'Lookup', 'Compare', 'Aggregate'}:
        return  # Unsupported/other shapes retain the existing execution refusal.
    allowed = {'entity', 'metric', 'period_phrase', 'modifier'}
    required = ['entity', 'metric']
    if kind == 'Compare':
        allowed |= {'compare_op', 'quantity'}
        required += ['compare_op', 'quantity']
    if kind == 'Aggregate':
        allowed |= {'aggregation', 'quarter_set', 'step', 'step_modifier', 'coverage'}
        required += ['aggregation']
    if any(types.count(role) != 1 for role in required) or any(t not in allowed for t in types):
        raise ValueError('native semantic roles are not all placed in the executable tree')
    if types.count('period_phrase') + types.count('quarter_set') != 1:
        raise ValueError('execution requires one completely bound fiscal scope')
    steps = [it for it in items if parser.view(it)['phrase_type'] in {'step', 'step_modifier'}]
    if kind == 'Aggregate' and (len(steps) > 1 or any(parser.view(it)['valid_phrase'] != 'quarter' for it in steps)):
        raise ValueError('incompatible or repeated collection step')
