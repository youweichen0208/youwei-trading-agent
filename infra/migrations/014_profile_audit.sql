-- Lifecycle evidence is append-only and excludes credentials and file paths.
CREATE TABLE profile_lifecycle_audit (
 sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 owner_id uuid NOT NULL,
 profile_id uuid NOT NULL,
 previous_state text, state text NOT NULL,
 previous_error text, error text,
 previous_container_id text, container_id text,
 deployment_hash text, managed boolean NOT NULL,
 database_actor text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(profile_id,owner_id) REFERENCES profile_instances(id,owner_id)
);
CREATE INDEX profile_lifecycle_audit_profile ON profile_lifecycle_audit(owner_id,profile_id,sequence);
CREATE TRIGGER immutable_profile_audit BEFORE UPDATE OR DELETE ON profile_lifecycle_audit
 FOR EACH ROW EXECUTE FUNCTION reject_execution_evidence_mutation();
CREATE TRIGGER immutable_profile_audit_truncate BEFORE TRUNCATE ON profile_lifecycle_audit
 FOR EACH STATEMENT EXECUTE FUNCTION reject_execution_evidence_mutation();

CREATE FUNCTION audit_profile_transition() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
 IF TG_OP='UPDATE' AND ROW(OLD.state,OLD.lifecycle_error,OLD.container_id,OLD.deployment_hash,OLD.lifecycle_managed)
   IS NOT DISTINCT FROM ROW(NEW.state,NEW.lifecycle_error,NEW.container_id,NEW.deployment_hash,NEW.lifecycle_managed) THEN
  RETURN NEW;
 END IF;
 INSERT INTO profile_lifecycle_audit(owner_id,profile_id,previous_state,state,previous_error,error,
   previous_container_id,container_id,deployment_hash,managed,database_actor)
 VALUES(NEW.owner_id,NEW.id,CASE WHEN TG_OP='UPDATE' THEN OLD.state END,NEW.state,
   CASE WHEN TG_OP='UPDATE' THEN OLD.lifecycle_error END,NEW.lifecycle_error,
   CASE WHEN TG_OP='UPDATE' THEN OLD.container_id END,NEW.container_id,
   NEW.deployment_hash,NEW.lifecycle_managed,session_user);
 RETURN NEW;
END $$;
DO $$
DECLARE app_schema text := current_schema();
BEGIN
 EXECUTE format('ALTER FUNCTION %I.audit_profile_transition() SET search_path=pg_catalog,%I,pg_temp',app_schema,app_schema);
 EXECUTE format('REVOKE ALL ON FUNCTION %I.audit_profile_transition() FROM PUBLIC',app_schema);
END $$;
CREATE TRIGGER audit_profile_state AFTER INSERT OR UPDATE ON profile_instances
 FOR EACH ROW EXECUTE FUNCTION audit_profile_transition();
