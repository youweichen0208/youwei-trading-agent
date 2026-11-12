-- Docker StartedAt is an opaque nanosecond-resolution observation, not proof
-- of an upstream run's process identity or exit by itself. Never backfill it.
ALTER TABLE profile_instances ADD COLUMN container_started_at text;
ALTER TABLE executions ADD COLUMN runtime_container_id text;
ALTER TABLE executions ADD COLUMN runtime_container_started_at text;
ALTER TABLE executions ADD COLUMN runtime_deployment_hash text;
ALTER TABLE profile_lifecycle_audit ADD COLUMN previous_container_started_at text;
ALTER TABLE profile_lifecycle_audit ADD COLUMN container_started_at text;

CREATE OR REPLACE FUNCTION audit_profile_transition() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
 IF TG_OP='UPDATE' AND ROW(OLD.state,OLD.lifecycle_error,OLD.container_id,OLD.deployment_hash,OLD.lifecycle_managed,OLD.container_started_at)
   IS NOT DISTINCT FROM ROW(NEW.state,NEW.lifecycle_error,NEW.container_id,NEW.deployment_hash,NEW.lifecycle_managed,NEW.container_started_at) THEN
  RETURN NEW;
 END IF;
 INSERT INTO profile_lifecycle_audit(owner_id,profile_id,previous_state,state,previous_error,error,
   previous_container_id,container_id,deployment_hash,managed,database_actor,previous_container_started_at,container_started_at)
 VALUES(NEW.owner_id,NEW.id,CASE WHEN TG_OP='UPDATE' THEN OLD.state END,NEW.state,
   CASE WHEN TG_OP='UPDATE' THEN OLD.lifecycle_error END,NEW.lifecycle_error,
   CASE WHEN TG_OP='UPDATE' THEN OLD.container_id END,NEW.container_id,
   NEW.deployment_hash,NEW.lifecycle_managed,session_user,
   CASE WHEN TG_OP='UPDATE' THEN OLD.container_started_at END,NEW.container_started_at);
 RETURN NEW;
END $$;
DO $$
DECLARE app_schema text := current_schema();
BEGIN
 EXECUTE format('ALTER FUNCTION %I.audit_profile_transition() SET search_path=pg_catalog,%I,pg_temp',app_schema,app_schema);
 EXECUTE format('REVOKE ALL ON FUNCTION %I.audit_profile_transition() FROM PUBLIC',app_schema);
END $$;
