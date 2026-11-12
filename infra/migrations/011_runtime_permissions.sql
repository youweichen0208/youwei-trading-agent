-- Keep owner serialization without granting runtime services UPDATE on users.
CREATE FUNCTION lock_task_owner(p_owner uuid) RETURNS boolean
 LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
 PERFORM id FROM users WHERE id=p_owner FOR UPDATE;
 RETURN FOUND;
END $$;
REVOKE ALL ON FUNCTION lock_task_owner(uuid) FROM PUBLIC;

-- A Worker may run the fixed retention policy, not arbitrary deletes or login
-- session updates. No caller-supplied cutoff, batch size or table name.
CREATE FUNCTION maintain_transient_records() RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE events_count integer; sessions_count integer; attempts_count integer; capabilities_count integer;
 app_schema text := (SELECT nspname FROM pg_namespace WHERE oid=(SELECT relnamespace FROM pg_class WHERE oid='tasks'::regclass));
BEGIN
 IF NOT pg_try_advisory_xact_lock(hashtext(current_database()),hashtext(app_schema || ':alpha_maintenance')) THEN
  RETURN jsonb_build_object('skipped',true);
 END IF;
 WITH expired AS (
  SELECT id FROM task_events WHERE created_at<now()-interval '7 days'
  ORDER BY created_at,id LIMIT 1000 FOR UPDATE SKIP LOCKED
 ) DELETE FROM task_events e USING expired x WHERE e.id=x.id;
 GET DIAGNOSTICS events_count = ROW_COUNT;
 WITH expired AS (
  SELECT token_hash FROM login_sessions WHERE expires_at<=now()
  ORDER BY expires_at,token_hash LIMIT 1000 FOR UPDATE SKIP LOCKED
 ) DELETE FROM login_sessions s USING expired x WHERE s.token_hash=x.token_hash;
 GET DIAGNOSTICS sessions_count = ROW_COUNT;
 WITH expired AS (
  SELECT bucket FROM auth_attempts WHERE reset_at<now()
  ORDER BY reset_at,bucket LIMIT 1000 FOR UPDATE SKIP LOCKED
 ) DELETE FROM auth_attempts a USING expired x WHERE a.bucket=x.bucket;
 GET DIAGNOSTICS attempts_count = ROW_COUNT;
 WITH retired AS (
  SELECT e.task_id FROM executions e JOIN tasks t ON t.id=e.task_id
  WHERE t.status IN ('completed','failed','cancelled','interrupted')
   AND (e.tool_token_hash IS NOT NULL OR e.tool_token_ciphertext IS NOT NULL)
  ORDER BY e.task_id LIMIT 1000 FOR UPDATE OF e SKIP LOCKED
 ) UPDATE executions e SET tool_token_hash=NULL,tool_token_ciphertext=NULL
  FROM retired r WHERE e.task_id=r.task_id;
 GET DIAGNOSTICS capabilities_count = ROW_COUNT;
 RETURN jsonb_build_object('skipped',false,'events',events_count,'sessions',sessions_count,
  'attempts',attempts_count,'capabilities',capabilities_count);
END $$;
REVOKE ALL ON FUNCTION maintain_transient_records() FROM PUBLIC;

-- Only triggers may append audit rows. Their fixed trusted search path prevents
-- temporary objects or caller search_path from redirecting privileged writes.
ALTER FUNCTION audit_task_transition() SECURITY DEFINER;
ALTER FUNCTION audit_tool_transition() SECURITY DEFINER;
DO $$
DECLARE app_schema text := current_schema(); fn text;
BEGIN
 EXECUTE format('ALTER FUNCTION %I.lock_task_owner(uuid) SET search_path=pg_catalog,%I,pg_temp',app_schema,app_schema);
 FOREACH fn IN ARRAY ARRAY['audit_task_transition','audit_tool_transition','maintain_transient_records'] LOOP
  EXECUTE format('ALTER FUNCTION %I.%I() SET search_path=pg_catalog,%I,pg_temp',app_schema,fn,app_schema);
  EXECUTE format('REVOKE ALL ON FUNCTION %I.%I() FROM PUBLIC',app_schema,fn);
 END LOOP;
END $$;
