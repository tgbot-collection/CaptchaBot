-- 扩展来源约束，保留原有样本 ID、审核状态及通知引用。
CREATE TABLE samples_group_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
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
    CHECK ((status = 'pending' AND label IS NULL)
        OR (status = 'reviewed' AND label IS NOT NULL AND label IN ('spam', 'ham')))
);
INSERT INTO samples_group_v2
    (id, schema_version, source, message_json, text, status, label,
     model_version, prediction, prediction_score, created_at, reviewed_at,
     reviewed_by, review_chat_id, review_message_id, dedupe_key)
SELECT id, schema_version, source, message_json, text, status, label,
       model_version, prediction, prediction_score, created_at, reviewed_at,
       reviewed_by, review_chat_id, review_message_id, dedupe_key
FROM samples;
DROP TABLE samples;
ALTER TABLE samples_group_v2 RENAME TO samples;
CREATE INDEX idx_samples_status_label ON samples(status, label);
CREATE INDEX idx_samples_reviewed_export ON samples(status, id);
