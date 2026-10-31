"""Deterministic English integer phrases, shared by lexical and quantity binding."""
ONES = dict(zip('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(), range(20)))
TENS = dict(zip('twenty thirty forty fifty sixty seventy eighty ninety'.split(), range(20, 100, 10)))


def integer(words):
    words = [w.casefold() for w in words]
    if len(words) == 1 and words[0] in ONES:
        return ONES[words[0]]
    if words and words[0] in TENS:
        if len(words) == 1:
            return TENS[words[0]]
        if len(words) == 2 and 0 < ONES.get(words[1], 0) < 10:
            return TENS[words[0]] + ONES[words[1]]
    if len(words) >= 2 and 0 < ONES.get(words[0], 0) < 10 and words[1] == 'hundred':
        rest = words[2:]
        if rest and rest[0] == 'and':
            rest = rest[1:]
        if not rest:
            return ONES[words[0]] * 100
        tail = integer(rest)
        if tail is not None and 0 < tail < 100:
            return ONES[words[0]] * 100 + tail
    return None


def compose(items, question, view):
    out, k = [], 0
    word = lambda it: question[it['ch0']:it['ch1']].casefold()
    while k < len(items):
        # Number words gain a numeric role only as a currency/scale quantity.
        best = None
        for end in range(k + 1, min(k + 6, len(items)) + 1):
            if any(view(it)['phrase_type'] not in {'unlisted', 'grammar', 'number_word'} for it in items[k:end]):
                break
            value = integer([word(it) for it in items[k:end]])
            bounded = (out and view(out[-1])['phrase_type'] == 'currency') or (end < len(items) and view(items[end])['phrase_type'] == 'scale')
            if value is not None and bounded:
                best = end, value
        if best:
            end, value = best
            out.append({**items[k], 'end': items[end - 1]['end'], 'ch1': items[end - 1]['ch1'],
                        'row': None, 'ptype': 'number', 'valid': str(value)})
            k = end
        else:
            out.append(items[k]); k += 1
    return out
