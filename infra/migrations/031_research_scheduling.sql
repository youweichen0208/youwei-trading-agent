ALTER TABLE tasks ADD COLUMN origin text NOT NULL DEFAULT 'interactive' CHECK(origin IN ('interactive','background'));
ALTER TABLE tasks ADD COLUMN priority integer NOT NULL DEFAULT 1000 CHECK(priority BETWEEN 0 AND 1000);
ALTER TABLE tasks ADD COLUMN budget_seconds integer NOT NULL DEFAULT 600 CHECK(budget_seconds BETWEEN 10 AND 1200);
CREATE INDEX task_priority_queue ON tasks(owner_id,priority DESC,ordinal) WHERE status='queued';
ALTER TABLE threads DROP CONSTRAINT threads_source_check;
ALTER TABLE threads ADD CONSTRAINT threads_source_check CHECK(source IN ('web','feishu_private','feishu_group','background'));
CREATE TABLE research_jobs (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), job_key text NOT NULL,
 symbol text NOT NULL, tier text NOT NULL CHECK(tier IN ('holding','watchlist','temporary','candidate','review','trial')),
 stage text NOT NULL DEFAULT 'collect' CHECK(stage IN ('collect','analyze','publish','review','trial')),
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('deferred','pending','running','completed','failed','blocked')),
 priority integer NOT NULL, session date NOT NULL, thread_id uuid,
 active_task_id uuid REFERENCES tasks(id), checkpoint jsonb NOT NULL DEFAULT '{}',
 error_code text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(owner_id,job_key), FOREIGN KEY(thread_id,owner_id) REFERENCES threads(id,owner_id)
);
ALTER TABLE tasks ADD COLUMN research_job_id uuid REFERENCES research_jobs(id);
CREATE INDEX research_jobs_ready ON research_jobs(owner_id,priority DESC,created_at) WHERE status IN ('deferred','pending','running');
CREATE TABLE research_schedule_runs (
 key text PRIMARY KEY, kind text NOT NULL, owner_id uuid REFERENCES users(id),
 status text NOT NULL CHECK(status IN ('running','completed','failed')),
 lease_until timestamptz NOT NULL, attempts integer NOT NULL DEFAULT 1,
 error_code text, created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz
);
CREATE TABLE research_checks (
 owner_id uuid NOT NULL REFERENCES users(id), symbol text NOT NULL,
 fingerprint text NOT NULL, checked_at timestamptz NOT NULL,
 payload jsonb NOT NULL DEFAULT '{}', PRIMARY KEY(owner_id,symbol)
);
CREATE TABLE notifications (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), kind text NOT NULL,
 event_key text NOT NULL, payload jsonb NOT NULL, read_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(owner_id,event_key)
);
CREATE INDEX notifications_owner_recent ON notifications(owner_id,created_at DESC);
ALTER TABLE feishu_outbox ALTER COLUMN inbox_key DROP NOT NULL;
ALTER TABLE feishu_outbox ADD COLUMN delivery_mode text NOT NULL DEFAULT 'reply' CHECK(delivery_mode IN ('reply','create'));
ALTER TABLE feishu_outbox ADD COLUMN notification_id uuid REFERENCES notifications(id);
ALTER TABLE feishu_outbox ADD COLUMN app_id text;
ALTER TABLE feishu_outbox ADD COLUMN receive_open_id text;
ALTER TABLE feishu_outbox ADD COLUMN message_id text;
ALTER TABLE feishu_outbox ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE feishu_outbox ADD CONSTRAINT feishu_delivery_target CHECK(
 (delivery_mode='reply' AND inbox_key IS NOT NULL AND notification_id IS NULL) OR
 (delivery_mode='create' AND inbox_key IS NULL AND notification_id IS NOT NULL AND app_id IS NOT NULL AND receive_open_id IS NOT NULL)
);
CREATE UNIQUE INDEX feishu_notification_delivery ON feishu_outbox(notification_id,app_id,part) WHERE notification_id IS NOT NULL;
REVOKE ALL ON research_jobs,research_schedule_runs,research_checks,notifications FROM PUBLIC;
