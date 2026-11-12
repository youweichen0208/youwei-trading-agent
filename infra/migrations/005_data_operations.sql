CREATE TABLE tool_operations (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL, task_id uuid NOT NULL,
 operation_key text NOT NULL, kind text NOT NULL CHECK(kind IN ('market','quant')),
 request_hash text NOT NULL, request jsonb NOT NULL,
 status text NOT NULL CHECK(status IN ('running','stopping','unknown','completed','failed','cancelled')),
 result jsonb, error_code text, started_at timestamptz NOT NULL DEFAULT now(),
 finished_at timestamptz,
 UNIQUE(task_id,operation_key),
 FOREIGN KEY(task_id,owner_id) REFERENCES tasks(id,owner_id)
);
CREATE INDEX active_tool_operations ON tool_operations(task_id,status)
 WHERE status IN ('running','stopping','unknown');
ALTER TABLE datasets ADD COLUMN operation_id uuid UNIQUE REFERENCES tool_operations(id);
ALTER TABLE results ADD COLUMN operation_id uuid UNIQUE REFERENCES tool_operations(id);
