ALTER TABLE profile_instances ADD COLUMN context_id uuid;
UPDATE profile_instances p SET context_id=pc.context_id FROM profile_contexts pc WHERE pc.profile_id=p.id;
ALTER TABLE profile_instances ALTER COLUMN context_id SET NOT NULL;
ALTER TABLE profile_instances ADD FOREIGN KEY(context_id,owner_id) REFERENCES execution_contexts(id,owner_id);
ALTER TABLE profile_instances DROP CONSTRAINT profile_instances_owner_id_role_key;
ALTER TABLE profile_instances ADD UNIQUE(context_id,role);
CREATE FUNCTION default_profile_context() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 NEW.context_id := coalesce(NEW.context_id,NEW.owner_id);
 RETURN NEW;
END $$;
CREATE TRIGGER default_profile_context BEFORE INSERT ON profile_instances FOR EACH ROW EXECUTE FUNCTION default_profile_context();
CREATE OR REPLACE FUNCTION bind_personal_context() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME='threads' THEN
  INSERT INTO thread_contexts(thread_id,owner_id,context_id) VALUES(NEW.id,NEW.owner_id,NEW.owner_id);
 ELSE
  INSERT INTO profile_contexts(profile_id,owner_id,context_id) VALUES(NEW.id,NEW.owner_id,NEW.context_id);
 END IF;
 RETURN NEW;
END $$;
-- Legacy column name owner_id now identifies the preparation context. Personal IDs are unchanged.
ALTER TABLE profile_preparation_jobs DROP CONSTRAINT profile_preparation_jobs_owner_id_fkey;
ALTER TABLE profile_preparation_jobs ADD FOREIGN KEY(owner_id) REFERENCES execution_contexts(id);
