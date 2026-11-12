-- One deployment host/port namespace per application schema. Existing users are
-- not silently enrolled: they may already have manually prepared credentials.
CREATE TABLE profile_preparation_jobs (
 owner_id uuid PRIMARY KEY REFERENCES users(id),
 request_id uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),
 status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','preparing','prepared','blocked','manual')),
 first_port integer CHECK(first_port BETWEEN 1024 AND 65533),
 settings_hash text CHECK(settings_hash ~ '^[a-f0-9]{64}$'),
 error_code text CHECK(error_code IN ('port_pool_exhausted','preparation_failed')),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK((first_port IS NULL) = (settings_hash IS NULL)),
 CHECK(status NOT IN ('preparing','prepared','manual') OR first_port IS NOT NULL)
);
CREATE TABLE profile_preparation_policy (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 root_identity text NOT NULL,
 settings_hash text NOT NULL CHECK(settings_hash ~ '^[a-f0-9]{64}$'),
 first_port integer NOT NULL CHECK(first_port BETWEEN 1024 AND 65533),
 last_port integer NOT NULL CHECK(last_port BETWEEN first_port+2 AND 65535)
);
CREATE TABLE profile_api_ports (
 port integer PRIMARY KEY CHECK(port BETWEEN 1024 AND 65535),
 owner_id uuid NOT NULL REFERENCES profile_preparation_jobs(owner_id),
 role text NOT NULL CHECK(role IN ('research','prediction','portfolio')),
 UNIQUE(owner_id,role)
);
CREATE FUNCTION guard_profile_preparation() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF TG_OP='INSERT' AND (NEW.status<>'queued' OR NEW.first_port IS NOT NULL OR NEW.error_code IS NOT NULL) THEN
  RAISE EXCEPTION 'invalid_initial_preparation';
 END IF;
 IF TG_OP='UPDATE' THEN
  IF ROW(OLD.owner_id,OLD.request_id,OLD.created_at) IS DISTINCT FROM ROW(NEW.owner_id,NEW.request_id,NEW.created_at)
   OR (OLD.first_port IS NOT NULL AND ROW(OLD.first_port,OLD.settings_hash) IS DISTINCT FROM ROW(NEW.first_port,NEW.settings_hash))
   OR (OLD.status IN ('prepared','manual') AND NEW IS DISTINCT FROM OLD)
   OR (NEW.status<>OLD.status AND NOT (
     (OLD.status='queued' AND NEW.status IN ('preparing','manual','blocked')) OR
     (OLD.status='preparing' AND NEW.status IN ('prepared','blocked')) OR
     (OLD.status='blocked' AND NEW.status='preparing'))) THEN
   RAISE EXCEPTION 'immutable_preparation_binding';
  END IF;
 END IF;
 NEW.updated_at := now();
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION guard_profile_preparation() FROM PUBLIC;
CREATE TRIGGER guard_profile_preparation BEFORE INSERT OR UPDATE ON profile_preparation_jobs
 FOR EACH ROW EXECUTE FUNCTION guard_profile_preparation();
CREATE TRIGGER immutable_preparation_job_delete BEFORE DELETE ON profile_preparation_jobs
 FOR EACH ROW EXECUTE FUNCTION reject_execution_evidence_mutation();
CREATE TRIGGER immutable_preparation_job_truncate BEFORE TRUNCATE ON profile_preparation_jobs
 FOR EACH STATEMENT EXECUTE FUNCTION reject_execution_evidence_mutation();
CREATE TRIGGER immutable_preparation_policy BEFORE UPDATE OR DELETE ON profile_preparation_policy
 FOR EACH ROW EXECUTE FUNCTION reject_execution_evidence_mutation();
CREATE TRIGGER immutable_preparation_policy_truncate BEFORE TRUNCATE ON profile_preparation_policy
 FOR EACH STATEMENT EXECUTE FUNCTION reject_execution_evidence_mutation();
CREATE TRIGGER immutable_profile_port BEFORE UPDATE OR DELETE ON profile_api_ports
 FOR EACH ROW EXECUTE FUNCTION reject_execution_evidence_mutation();
CREATE TRIGGER immutable_profile_port_truncate BEFORE TRUNCATE ON profile_api_ports
 FOR EACH STATEMENT EXECUTE FUNCTION reject_execution_evidence_mutation();
CREATE INDEX profile_preparation_pending ON profile_preparation_jobs(created_at) WHERE status IN ('queued','preparing');
