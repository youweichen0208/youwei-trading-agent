-- Additive foundation only; external admission stays disabled until context isolation ships.
ALTER TABLE users ALTER COLUMN email DROP NOT NULL;
ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;
ALTER TABLE users ADD COLUMN display_name text;
CREATE TABLE feishu_identities (
 app_id text NOT NULL CHECK(length(app_id)>0),
 open_id text NOT NULL CHECK(length(open_id)>0),
 owner_id uuid NOT NULL REFERENCES users(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(app_id,open_id), UNIQUE(app_id,owner_id)
);
REVOKE ALL ON feishu_identities FROM PUBLIC;
