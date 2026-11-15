-- Append-only policy generations; the legacy singleton remains evidence.
CREATE TABLE profile_preparation_policy_versions (
 generation integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 root_identity text NOT NULL,
 settings_hash text NOT NULL CHECK(settings_hash ~ '^[a-f0-9]{64}$'),
 first_port integer NOT NULL CHECK(first_port BETWEEN 1024 AND 65533),
 last_port integer NOT NULL CHECK(last_port BETWEEN first_port+2 AND 65535),
 created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO profile_preparation_policy_versions(root_identity,settings_hash,first_port,last_port)
 SELECT root_identity,settings_hash,first_port,last_port FROM profile_preparation_policy;
CREATE TRIGGER immutable_preparation_version BEFORE UPDATE OR DELETE ON profile_preparation_policy_versions
 FOR EACH ROW EXECUTE FUNCTION reject_execution_evidence_mutation();
CREATE TRIGGER immutable_preparation_version_truncate BEFORE TRUNCATE ON profile_preparation_policy_versions
 FOR EACH STATEMENT EXECUTE FUNCTION reject_execution_evidence_mutation();
