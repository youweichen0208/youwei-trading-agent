ALTER TABLE executions ADD COLUMN tool_token_hash text UNIQUE;
ALTER TABLE executions ADD COLUMN tool_token_ciphertext text;
ALTER TABLE tasks ADD COLUMN workspace_id uuid;
UPDATE tasks t SET workspace_id=w.id,workspace_revision=coalesce(t.workspace_revision,w.revision)
 FROM workspaces w WHERE w.thread_id=t.thread_id AND w.owner_id=t.owner_id;
ALTER TABLE tasks ALTER COLUMN workspace_id SET NOT NULL;
ALTER TABLE tasks ALTER COLUMN workspace_revision SET NOT NULL;
ALTER TABLE tasks ADD FOREIGN KEY(workspace_id,owner_id) REFERENCES workspaces(id,owner_id);
CREATE TABLE tool_calls (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL, task_id uuid NOT NULL, operation_key text NOT NULL,
 tool text NOT NULL, request_hash text NOT NULL, response jsonb,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(task_id,operation_key), FOREIGN KEY(task_id,owner_id) REFERENCES tasks(id,owner_id)
);
ALTER TABLE results ADD COLUMN gateway_call_id uuid UNIQUE REFERENCES tool_calls(id);
CREATE TABLE task_artifacts (
 id uuid PRIMARY KEY,owner_id uuid NOT NULL,root_id uuid NOT NULL,source_task_id uuid NOT NULL,
 body jsonb NOT NULL,removed boolean NOT NULL DEFAULT false,
 FOREIGN KEY(root_id,owner_id) REFERENCES tasks(id,owner_id),
 FOREIGN KEY(source_task_id,owner_id) REFERENCES tasks(id,owner_id)
);
CREATE INDEX task_artifacts_root ON task_artifacts(root_id);
