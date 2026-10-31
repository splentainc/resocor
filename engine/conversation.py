"""Bounded dialogue grammar over checked questions; no facts live in state."""
from copy import deepcopy
import re

from ._legacy import execution_module

OPENING = re.compile(r'^(?:and|what about|how about|same for)\s+(.+)$', re.I | re.S)
GLUE = {'in', 'for', 'of', 'the', '?', '.', '!'}
SLOTS = {'entity', 'metric', 'period'}


def resolve(question, state):
    """Consume every follow-up token and substitute only typed source spans.

    Supported: explicit follow-up opener plus entity/metric/four-digit fiscal
    year slots, or a bare single slot. Other clauses stay on the normal engine
    route; an explicit unsupported follow-up asks for a complete question.
    """
    legacy = execution_module()
    opening = OPENING.fullmatch(question.strip())
    payload = opening[1] if opening else question.strip()
    annotation = legacy.step1(payload, 0).model_dump(by_alias=True, exclude_none=True)
    values, invalid = {}, False
    for segment in annotation['segments']:
        kind, text = segment['type'], segment['text'].strip()
        if kind in SLOTS:
            if kind in values or (kind == 'period' and not re.fullmatch(r'\d{4}', text)):
                invalid = True
            values[kind] = text
        elif kind != 'filler' or any(t.lower() not in GLUE for t, _, _ in legacy.tokenize(text)):
            invalid = True
    # Bare multi-slot text is not a follow-up contract; keep the full parser's
    # decision. Unknown/meaningful words are never stripped to rescue a request.
    candidate = bool(opening) or (not invalid and len(values) == 1)
    result = {'status': 'standalone', 'original_query': question, 'resolved_query': question,
              'context_from_turn': None, 'inherited': {}, 'replacements': {}}
    if not candidate:
        return result
    result['followup_grammar'] = {'opening': question.strip()[:opening.start(1)] if opening else '',
                                  'payload_segments':annotation['segments']}
    if invalid or not values:
        return {**result, 'status': 'clarify', 'reason': 'Please give a complete question; this follow-up contains unsupported or ambiguous meaning.'}
    context = state.get('context')
    if not context:
        return {**result, 'status': 'clarify', 'reason': 'There is no active factual question to continue. Please give the company, metric, period and operation.'}
    base, replacements, offset = context['resolved_query'], [], 0
    found = {key: 0 for key in values}
    for segment in context['annotation']['segments']:
        kind, text = segment['type'], segment['text']
        if kind in values:
            if kind != 'period' or re.fullmatch(r'\s*\d{4}\s*', text):
                start = offset + len(text) - len(text.lstrip())
                end = offset + len(text.rstrip())
                replacements.append((start, end, values[kind]))
                found[kind] += 1
        offset += len(text)
    if any(count != 1 for count in found.values()):
        return {**result, 'status': 'clarify', 'reason': 'The previous question has no single matching slot. Please give a complete question.'}
    for start, end, text in sorted(replacements, reverse=True):
        base = base[:start] + text + base[end:]
    if len(base) > 4096:
        return {**result, 'status': 'clarify', 'reason': 'The expanded follow-up exceeds the question limit.'}
    return {**result, 'status': 'resolved', 'resolved_query': base,
            'context_from_turn': context['turn_id'], 'replacements': values,
            'inherited': {k:v for k,v in context['identity'].items() if k not in values}}


def advance(state, turn):
    updated = deepcopy(state)
    result = turn['result']
    receipt = result.get('execution') or {}
    if result.get('outcome') == 'answered' and receipt.get('plan'):
        plan = receipt['plan']
        case = plan['cases'][0]
        updated['context'] = {'turn_id': turn['id'], 'resolved_query': turn['resolved_query'],
                              'annotation': receipt['annotation'],
                              'identity': {'entity':case['company'], 'metric':case['metric'],
                                           'period':str(case['period']['fiscal_year']),
                                           'operation':plan['op'], 'limit':plan.get('limit')}}
        updated['idle'] = 0
    elif result.get('route') == 'chatter':
        updated['idle'] = state.get('idle', 0) + 1
        if updated['idle'] >= 3:
            updated['context'] = None
    else:
        # A refused/ambiguous topic cannot leave an older topic active.
        updated.update(context=None, idle=0)
    updated['last_turn_id'] = turn['id']
    return updated
