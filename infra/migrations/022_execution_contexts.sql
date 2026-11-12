-- Preserve existing Profile IDs and immutable facts; record context ownership separately.
CREATE TABLE execution_contexts (
 id uuid PRIMARY KEY,
 owner_id uuid NOT NULL REFERENCES users(id),
 kind text NOT NULL CHECK(kind IN ('personal','group')),
 app_id text,
 chat_id text,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,owner_id),
 CHECK((kind='personal' AND app_id IS NULL AND chat_id IS NULL) OR
       (kind='group' AND length(app_id)>0 AND length(chat_id)>0))
);
CREATE UNIQUE INDEX one_personal_context ON execution_contexts(owner_id) WHERE kind='personal';
CREATE UNIQUE INDEX one_group_context ON execution_contexts(owner_id,app_id,chat_id) WHERE kind='group';
-- Using owner UUID for the personal context preserves existing directory identity.
INSERT INTO execution_contexts(id,owner_id,kind) SELECT id,id,'personal' FROM users;
CREATE TABLE thread_contexts (
 thread_id uuid PRIMARY KEY,
 owner_id uuid NOT NULL,
 context_id uuid NOT NULL,
 FOREIGN KEY(thread_id,owner_id) REFERENCES threads(id,owner_id),
 FOREIGN KEY(context_id,owner_id) REFERENCES execution_contexts(id,owner_id)
);
INSERT INTO thread_contexts SELECT id,owner_id,owner_id FROM threads;
CREATE TABLE profile_contexts (
 profile_id uuid PRIMARY KEY,
 owner_id uuid NOT NULL,
 context_id uuid NOT NULL,
 FOREIGN KEY(profile_id,owner_id) REFERENCES profile_instances(id,owner_id),
 FOREIGN KEY(context_id,owner_id) REFERENCES execution_contexts(id,owner_id)
);
INSERT INTO profile_contexts SELECT id,owner_id,owner_id FROM profile_instances;
CREATE FUNCTION create_personal_context() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO execution_contexts(id,owner_id,kind) VALUES(NEW.id,NEW.id,'personal');
 RETURN NEW;
END $$;
CREATE TRIGGER user_personal_context AFTER INSERT ON users FOR EACH ROW EXECUTE FUNCTION create_personal_context();
CREATE FUNCTION bind_personal_context() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME='threads' THEN
  INSERT INTO thread_contexts(thread_id,owner_id,context_id) VALUES(NEW.id,NEW.owner_id,NEW.owner_id);
 ELSE
  INSERT INTO profile_contexts(profile_id,owner_id,context_id) VALUES(NEW.id,NEW.owner_id,NEW.owner_id);
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER thread_personal_context AFTER INSERT ON threads FOR EACH ROW EXECUTE FUNCTION bind_personal_context();
CREATE TRIGGER profile_personal_context AFTER INSERT ON profile_instances FOR EACH ROW EXECUTE FUNCTION bind_personal_context();
REVOKE ALL ON execution_contexts,thread_contexts,profile_contexts FROM PUBLIC;
