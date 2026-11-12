CREATE TABLE feishu_chats (
 key text PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id),
 thread_id uuid, FOREIGN KEY(thread_id,owner_id) REFERENCES threads(id,owner_id)
);
CREATE TABLE feishu_inbox (
 key text PRIMARY KEY, chat_key text NOT NULL REFERENCES feishu_chats(key),
 message_id text NOT NULL, chat_id text NOT NULL, input text NOT NULL,
 task_id uuid REFERENCES tasks(id), processed boolean NOT NULL DEFAULT false,
 finished boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE feishu_outbox (
 id uuid PRIMARY KEY, inbox_key text NOT NULL REFERENCES feishu_inbox(key),
 phase text NOT NULL, part integer NOT NULL, text text NOT NULL,
 sent boolean NOT NULL DEFAULT false, first_attempt timestamptz,
 next_attempt timestamptz NOT NULL DEFAULT now(), uncertain boolean NOT NULL DEFAULT false,
 UNIQUE(inbox_key,phase,part)
);
REVOKE ALL ON feishu_chats,feishu_inbox,feishu_outbox FROM PUBLIC;
