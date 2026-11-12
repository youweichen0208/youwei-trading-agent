CREATE TABLE feishu_oauth_states (
 state_hash text PRIMARY KEY,
 browser_hash text NOT NULL,
 verifier text NOT NULL,
 expires_at timestamptz NOT NULL DEFAULT now()+interval '5 minutes',
 consumed_at timestamptz
);
REVOKE ALL ON feishu_oauth_states FROM PUBLIC;
