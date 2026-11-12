CREATE INDEX task_events_retention ON task_events(created_at,id);
CREATE INDEX login_sessions_expiry ON login_sessions(expires_at,token_hash);
CREATE INDEX auth_attempts_expiry ON auth_attempts(reset_at,bucket);
CREATE INDEX owner_pending_roots ON tasks(owner_id)
 WHERE parent_id IS NULL AND status IN ('queued','running','stopping','unknown');
