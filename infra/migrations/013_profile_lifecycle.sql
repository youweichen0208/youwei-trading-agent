ALTER TABLE profile_instances ADD COLUMN lifecycle_managed boolean NOT NULL DEFAULT false;
ALTER TABLE profile_instances ADD COLUMN deployment_hash text;
ALTER TABLE profile_instances ADD COLUMN container_id text;
ALTER TABLE profile_instances ADD COLUMN last_used_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE profile_instances ADD COLUMN lifecycle_error text;
