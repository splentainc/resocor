PRAGMA user_version = 1;

CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE generic_chatter (
    id INTEGER PRIMARY KEY,
    user_phrase TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK (length(trim(user_phrase)) > 0),
    role TEXT NOT NULL CHECK (role IN ('greeting', 'gratitude', 'farewell', 'politeness', 'request_frame')),
    allow_standalone INTEGER NOT NULL CHECK (allow_standalone IN (0, 1)),
    allow_prefix INTEGER NOT NULL CHECK (allow_prefix IN (0, 1)),
    allow_suffix INTEGER NOT NULL CHECK (allow_suffix IN (0, 1)),
    response TEXT,
    review TEXT NOT NULL CHECK (review = 'author-reviewed'),
    source TEXT NOT NULL,
    CHECK (allow_standalone + allow_prefix + allow_suffix > 0)
) STRICT;
