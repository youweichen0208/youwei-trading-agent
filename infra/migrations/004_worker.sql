ALTER TABLE tasks ADD COLUMN next_attempt_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE executions ADD COLUMN runtime_identity text;
ALTER TABLE executions ADD COLUMN submission_started_at timestamptz;
ALTER TABLE executions ADD COLUMN terminal_receipt jsonb;
CREATE INDEX worker_ready ON tasks(next_attempt_at,ordinal)
 WHERE status IN ('queued','running','stopping','unknown');
