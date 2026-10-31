"""Conservative alias matching: one edit, at least five characters, one meaning."""


def edit_distance(a, b):
    """Optimal string alignment distance, including adjacent transposition."""
    d = [[i + j if i * j == 0 else 0 for j in range(len(b) + 1)] for i in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


def alias_row(text, glossary, phrase_type=None):
    word = text.lower().strip()
    if len(word) < 5:
        return None
    near = []
    for i, row in enumerate(glossary):
        alias = row["user_phrase"].lower()
        if row["phrase_type"] not in ("entity", "metric") or phrase_type and row["phrase_type"] != phrase_type:
            continue
        if row["match"] != "phrase" or row["valid_phrase"].startswith("not_held:"):
            continue
        if len(alias) >= 5 and abs(len(alias) - len(word)) <= 1 and alias.count(" ") == word.count(" ") and edit_distance(alias, word) <= 1:
            near.append(i)
    meanings = {(glossary[i]["phrase_type"], glossary[i]["valid_phrase"]) for i in near}
    return near[0] if len(meanings) == 1 else None
