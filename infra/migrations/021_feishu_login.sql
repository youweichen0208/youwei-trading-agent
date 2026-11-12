CREATE TABLE feishu_login_challenges (
 id uuid PRIMARY KEY,
 code_hash text NOT NULL UNIQUE,
 browser_hash text NOT NULL,
 owner_id uuid REFERENCES users(id),
 expires_at timestamptz NOT NULL DEFAULT now()+interval '5 minutes',
 confirmed_at timestamptz,
 consumed_at timestamptz
);
REVOKE ALL ON feishu_login_challenges FROM PUBLIC;
