CREATE TABLE runtime_recoveries (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL, task_id uuid NOT NULL UNIQUE,
 profile_id uuid NOT NULL, runtime_identity text NOT NULL,
 container_id text NOT NULL, deployment_hash text NOT NULL,
 run_id text NOT NULL, session_id text NOT NULL,
 admission_id text NOT NULL CHECK(length(admission_id) BETWEEN 1 AND 255), native_generation integer NOT NULL CHECK(native_generation>=0),
 phase text NOT NULL CHECK(phase IN ('stopping','stopped','starting','started','resolving','resolved')),
 stopped_container_started_at text, restarted_container_started_at text,
 native_receipt jsonb, created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(task_id,owner_id) REFERENCES tasks(id,owner_id),
 FOREIGN KEY(profile_id,owner_id) REFERENCES profile_instances(id,owner_id)
);
ALTER TABLE execution_audit ADD COLUMN recovery_id uuid REFERENCES runtime_recoveries(id);
ALTER TABLE execution_audit ADD COLUMN database_actor text;

CREATE FUNCTION guard_runtime_recovery() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE phases text[] := ARRAY['stopping','stopped','starting','started','resolving','resolved'];
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.phase<>'stopping' THEN RAISE EXCEPTION 'invalid_recovery_phase'; END IF;
 ELSIF ROW(OLD.id,OLD.owner_id,OLD.task_id,OLD.profile_id,OLD.runtime_identity,OLD.container_id,
    OLD.deployment_hash,OLD.run_id,OLD.session_id,OLD.admission_id,OLD.native_generation,OLD.created_at)
   IS DISTINCT FROM ROW(NEW.id,NEW.owner_id,NEW.task_id,NEW.profile_id,NEW.runtime_identity,NEW.container_id,
    NEW.deployment_hash,NEW.run_id,NEW.session_id,NEW.admission_id,NEW.native_generation,NEW.created_at)
   OR array_position(phases,NEW.phase)<>array_position(phases,OLD.phase)+1 THEN
  RAISE EXCEPTION 'immutable_recovery_identity_or_phase';
 END IF;
 IF TG_OP='UPDATE' AND (
   (OLD.stopped_container_started_at IS NOT NULL AND OLD.stopped_container_started_at IS DISTINCT FROM NEW.stopped_container_started_at)
   OR (OLD.restarted_container_started_at IS NOT NULL AND OLD.restarted_container_started_at IS DISTINCT FROM NEW.restarted_container_started_at)) THEN
  RAISE EXCEPTION 'immutable_recovery_observation';
 END IF;
 IF (array_position(phases,NEW.phase)>=2 AND NEW.stopped_container_started_at IS NULL)
   OR (array_position(phases,NEW.phase)>=4 AND (NEW.restarted_container_started_at IS NULL
     OR NEW.restarted_container_started_at=NEW.stopped_container_started_at)) THEN
  RAISE EXCEPTION 'recovery_observation_required';
 END IF;
 IF NEW.phase='resolved' AND (NEW.native_receipt IS NULL OR
   coalesce(NEW.native_receipt->>'status','') NOT IN ('completed','failed','cancelled','interrupted') OR
   NEW.native_receipt->>'run_id' IS DISTINCT FROM NEW.run_id OR
   NEW.native_receipt->>'session_id' IS DISTINCT FROM NEW.session_id OR
   NEW.native_receipt->>'admission_id' IS DISTINCT FROM NEW.admission_id::text OR
   NEW.native_receipt->>'execution_generation' IS DISTINCT FROM NEW.native_generation::text) THEN
  RAISE EXCEPTION 'native_receipt_required';
 END IF;
 NEW.updated_at := now();
 RETURN NEW;
END $$;
CREATE TRIGGER guard_runtime_recovery BEFORE INSERT OR UPDATE ON runtime_recoveries
 FOR EACH ROW EXECUTE FUNCTION guard_runtime_recovery();
CREATE TRIGGER immutable_runtime_recovery_delete BEFORE DELETE ON runtime_recoveries
 FOR EACH ROW EXECUTE FUNCTION reject_execution_evidence_mutation();
CREATE TRIGGER immutable_runtime_recovery_truncate BEFORE TRUNCATE ON runtime_recoveries
 FOR EACH STATEMENT EXECUTE FUNCTION reject_execution_evidence_mutation();

CREATE FUNCTION audit_runtime_recovery() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
 INSERT INTO execution_audit(owner_id,task_id,event,status,reason,generation,recovery_id,database_actor)
 SELECT NEW.owner_id,NEW.task_id,'recovery.' || NEW.phase,t.status,'operator_interrupt_unknown',
   t.generation,NEW.id,session_user FROM tasks t WHERE t.id=NEW.task_id AND t.owner_id=NEW.owner_id;
 RETURN NEW;
END $$;
DO $$
DECLARE app_schema text := current_schema();
BEGIN
 EXECUTE format('ALTER FUNCTION %I.audit_runtime_recovery() SET search_path=pg_catalog,%I,pg_temp',app_schema,app_schema);
 EXECUTE format('ALTER FUNCTION %I.guard_runtime_recovery() SET search_path=pg_catalog,pg_temp',app_schema);
 EXECUTE format('REVOKE ALL ON FUNCTION %I.audit_runtime_recovery() FROM PUBLIC',app_schema);
 EXECUTE format('REVOKE ALL ON FUNCTION %I.guard_runtime_recovery() FROM PUBLIC',app_schema);
END $$;
CREATE TRIGGER audit_runtime_recovery AFTER INSERT OR UPDATE ON runtime_recoveries
 FOR EACH ROW EXECUTE FUNCTION audit_runtime_recovery();
