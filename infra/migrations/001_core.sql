CREATE TABLE users (
 id uuid PRIMARY KEY, email text NOT NULL UNIQUE, password_hash text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE threads (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id),
 title text NOT NULL DEFAULT '新研究', created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,owner_id)
);
CREATE TABLE workspaces (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL, thread_id uuid NOT NULL UNIQUE,
 version integer NOT NULL DEFAULT 0, revision integer NOT NULL DEFAULT 0,
 snapshot jsonb NOT NULL DEFAULT '{"artifacts":[]}',
 FOREIGN KEY(thread_id,owner_id) REFERENCES threads(id,owner_id), UNIQUE(id,owner_id)
);
CREATE TABLE tasks (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL, thread_id uuid NOT NULL,
 parent_id uuid, root_id uuid NOT NULL, role text NOT NULL CHECK(role IN ('research','prediction','portfolio')),
 request_key text NOT NULL, request_hash text NOT NULL, input jsonb NOT NULL,
 status text NOT NULL DEFAULT 'queued' CHECK(status IN
 ('queued','running','stopping','unknown','completed','failed','cancelled','interrupted')),
 ordinal bigint GENERATED ALWAYS AS IDENTITY,
 generation integer NOT NULL DEFAULT 0, lease_owner uuid, lease_until timestamptz,
 deadline timestamptz NOT NULL DEFAULT now()+interval '10 minutes',
 cancel_requested_at timestamptz, error_code text, output jsonb,
 sequence integer NOT NULL DEFAULT 0, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,owner_id), UNIQUE(owner_id,request_key),
 FOREIGN KEY(thread_id,owner_id) REFERENCES threads(id,owner_id),
 FOREIGN KEY(parent_id,owner_id) REFERENCES tasks(id,owner_id),
 FOREIGN KEY(root_id,owner_id) REFERENCES tasks(id,owner_id),
 CHECK ((parent_id IS NULL AND root_id=id AND role='research') OR
        (parent_id IS NOT NULL AND root_id=parent_id AND role<>'research'))
);
CREATE UNIQUE INDEX one_child_per_role ON tasks(parent_id,role) WHERE parent_id IS NOT NULL;
CREATE UNIQUE INDEX one_active_profile ON tasks(owner_id,role)
 WHERE status IN ('running','stopping','unknown');
CREATE INDEX task_queue ON tasks(status,ordinal);
CREATE TABLE executions (
 task_id uuid PRIMARY KEY REFERENCES tasks(id), idempotency_key uuid NOT NULL UNIQUE,
 request jsonb NOT NULL, profile_id text NOT NULL, session_id text NOT NULL,
 upstream_run_id text, prepared_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE messages (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL, thread_id uuid NOT NULL,
 task_id uuid NOT NULL, role text NOT NULL CHECK(role IN ('user','assistant')),
 content text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(thread_id,owner_id) REFERENCES threads(id,owner_id),
 FOREIGN KEY(task_id,owner_id) REFERENCES tasks(id,owner_id), UNIQUE(task_id,role)
);
CREATE TABLE task_events (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL, task_id uuid NOT NULL,
 sequence integer NOT NULL, event text NOT NULL, data jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(task_id,owner_id) REFERENCES tasks(id,owner_id), UNIQUE(task_id,sequence)
);
CREATE TABLE datasets (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), metadata jsonb NOT NULL,
 payload jsonb NOT NULL, content_hash text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,owner_id)
);
CREATE TABLE results (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL, task_id uuid NOT NULL,
 kind text NOT NULL, payload jsonb NOT NULL, lineage jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(task_id,owner_id) REFERENCES tasks(id,owner_id), UNIQUE(id,owner_id)
);
CREATE FUNCTION reject_fact_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'immutable_fact'; END $$;
CREATE TRIGGER immutable_datasets BEFORE UPDATE OR DELETE ON datasets
 FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation();
CREATE TRIGGER immutable_results BEFORE UPDATE OR DELETE ON results
 FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation();
