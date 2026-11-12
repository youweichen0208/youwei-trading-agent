CREATE TABLE login_sessions (
 token_hash text PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id),
 expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE profile_instances (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id),
 role text NOT NULL CHECK(role IN ('research','prediction','portfolio')),
 state text NOT NULL DEFAULT 'unprovisioned', UNIQUE(owner_id,role), UNIQUE(id,owner_id)
);
CREATE TABLE profile_sessions (
 owner_id uuid NOT NULL, thread_id uuid NOT NULL, profile_id uuid NOT NULL,
 hermes_session_id uuid NOT NULL UNIQUE,
 PRIMARY KEY(thread_id,profile_id),
 FOREIGN KEY(thread_id,owner_id) REFERENCES threads(id,owner_id),
 FOREIGN KEY(profile_id,owner_id) REFERENCES profile_instances(id,owner_id)
);
CREATE TABLE auth_attempts (
 bucket text PRIMARY KEY, count integer NOT NULL, reset_at timestamptz NOT NULL
);
