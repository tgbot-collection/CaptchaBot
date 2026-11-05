CREATE TABLE IF NOT EXISTS samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_version INTEGER NOT NULL CHECK (schema_version = 2),
    source TEXT NOT NULL CHECK (source IN ('rule', 'manual', 'group')),
    message_json TEXT NOT NULL CHECK (json_valid(message_json)),
    text TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'reviewed')),
    label TEXT CHECK (label IN ('spam', 'ham')),
    model_version TEXT,
    prediction TEXT,
    prediction_score REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TEXT,
    reviewed_by INTEGER,
    review_chat_id INTEGER,
    review_message_id INTEGER,
    dedupe_key TEXT NOT NULL UNIQUE,
    CHECK (
        (status = 'pending' AND label IS NULL)
        OR (status = 'reviewed' AND label IS NOT NULL AND label IN ('spam', 'ham'))
    )
);

CREATE INDEX IF NOT EXISTS idx_samples_status_label
    ON samples(status, label);

CREATE INDEX IF NOT EXISTS idx_samples_reviewed_export
    ON samples(status, id);
