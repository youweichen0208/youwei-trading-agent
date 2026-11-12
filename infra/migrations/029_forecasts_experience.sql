-- Forward statements are immutable; resolution, reviews and experience versions append evidence.
CREATE TABLE forecasts (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), task_id uuid NOT NULL,
 result_id uuid NOT NULL UNIQUE REFERENCES results(id), operation_key text NOT NULL, request_hash text NOT NULL,
 symbol text NOT NULL, horizon integer NOT NULL CHECK(horizon IN (5,20)), event_key text NOT NULL,
 research_context_id uuid NOT NULL, published_at timestamptz NOT NULL,
 start_session date NOT NULL, target_session date NOT NULL, start_open_at timestamptz NOT NULL,
 target_close_at timestamptz NOT NULL, evaluate_after timestamptz NOT NULL,
 supersedes_id uuid REFERENCES forecasts(id), payload jsonb NOT NULL,
 UNIQUE(task_id,operation_key), UNIQUE(id,owner_id),
 FOREIGN KEY(task_id,owner_id) REFERENCES tasks(id,owner_id), CHECK(target_session>=start_session)
);
CREATE INDEX forecasts_due ON forecasts(evaluate_after);
CREATE UNIQUE INDEX forecasts_original_event ON forecasts(owner_id,symbol,horizon,event_key,start_session) WHERE supersedes_id IS NULL;
CREATE TABLE forecast_evaluations (
 id uuid PRIMARY KEY, forecast_id uuid NOT NULL UNIQUE REFERENCES forecasts(id),
 owner_id uuid NOT NULL REFERENCES users(id), result_id uuid NOT NULL UNIQUE REFERENCES results(id),
 payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE forecast_checks (
 forecast_id uuid PRIMARY KEY REFERENCES forecasts(id), checked_at timestamptz NOT NULL,
 reason text NOT NULL
);
CREATE TABLE forecast_reviews (
 id uuid PRIMARY KEY, forecast_id uuid NOT NULL REFERENCES forecasts(id), owner_id uuid NOT NULL,
 task_id uuid NOT NULL, result_id uuid NOT NULL UNIQUE REFERENCES results(id), operation_key text NOT NULL,
 request_hash text NOT NULL, payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(task_id,operation_key), FOREIGN KEY(task_id,owner_id) REFERENCES tasks(id,owner_id)
);
CREATE TABLE experience_lessons (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), title text NOT NULL,
 scope text NOT NULL CHECK(scope IN ('company','industry','general')),
 status text NOT NULL CHECK(status IN ('candidate','shadow','active','retired')),
 current_version_id uuid, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE experience_versions (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), lesson_id uuid NOT NULL REFERENCES experience_lessons(id),
 review_id uuid NOT NULL REFERENCES forecast_reviews(id), horizon integer NOT NULL CHECK(horizon IN (5,20)),
 symbol text, event_type text NOT NULL, statement text NOT NULL, applicability text NOT NULL,
 invalidation text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE experience_lessons ADD CONSTRAINT experience_current_version_fk
 FOREIGN KEY(current_version_id) REFERENCES experience_versions(id);
CREATE TABLE experience_trials (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), task_id uuid,
 lesson_version_id uuid NOT NULL REFERENCES experience_versions(id), research_context_id uuid NOT NULL,
 symbol text NOT NULL, horizon integer NOT NULL CHECK(horizon IN (5,20)), event_key text NOT NULL,
 operation_key text NOT NULL, request_hash text NOT NULL, published_at timestamptz NOT NULL,
 start_session date NOT NULL,target_session date NOT NULL,evaluate_after timestamptz NOT NULL,
 payload jsonb NOT NULL, UNIQUE(task_id,operation_key), UNIQUE(owner_id,lesson_version_id,symbol,event_key),
 FOREIGN KEY(task_id,owner_id) REFERENCES tasks(id,owner_id)
);
CREATE TABLE experience_trial_evaluations (
 trial_id uuid PRIMARY KEY REFERENCES experience_trials(id), payload jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE experience_batches (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), lesson_version_id uuid NOT NULL REFERENCES experience_versions(id),
 trial_ids uuid[] NOT NULL, previous_status text NOT NULL, resulting_status text NOT NULL,
 payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER immutable_forecasts BEFORE UPDATE OR DELETE ON forecasts FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation();
CREATE TRIGGER immutable_forecast_evaluations BEFORE UPDATE OR DELETE ON forecast_evaluations FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation();
CREATE TRIGGER immutable_forecast_reviews BEFORE UPDATE OR DELETE ON forecast_reviews FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation();
CREATE TRIGGER immutable_experience_versions BEFORE UPDATE OR DELETE ON experience_versions FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation();
CREATE TRIGGER immutable_experience_trials BEFORE UPDATE OR DELETE ON experience_trials FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation();
CREATE TRIGGER immutable_experience_trial_evaluations BEFORE UPDATE OR DELETE ON experience_trial_evaluations FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation();
CREATE TRIGGER immutable_experience_batches BEFORE UPDATE OR DELETE ON experience_batches FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation();

CREATE TABLE experience_trial_arm_bindings (
 trial_id uuid NOT NULL REFERENCES experience_trials(id), arm text NOT NULL CHECK(arm IN ('control','candidate')),
 owner_id uuid NOT NULL REFERENCES users(id), thread_id uuid NOT NULL REFERENCES threads(id),
 PRIMARY KEY(trial_id,arm), UNIQUE(thread_id)
);
CREATE TABLE experience_trial_arms (
 trial_id uuid NOT NULL REFERENCES experience_trials(id), arm text NOT NULL CHECK(arm IN ('control','candidate')),
 owner_id uuid NOT NULL REFERENCES users(id), task_id uuid NOT NULL REFERENCES tasks(id),
 operation_key text NOT NULL,request_hash text NOT NULL,payload jsonb NOT NULL,created_at timestamptz NOT NULL,
 PRIMARY KEY(trial_id,arm),UNIQUE(task_id,operation_key)
);
CREATE TABLE experience_trial_checks (
 trial_id uuid PRIMARY KEY REFERENCES experience_trials(id),checked_at timestamptz NOT NULL,reason text NOT NULL
);
CREATE TRIGGER immutable_experience_trial_arms BEFORE UPDATE OR DELETE ON experience_trial_arms FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation();
CREATE TRIGGER immutable_experience_trial_arm_bindings BEFORE UPDATE OR DELETE ON experience_trial_arm_bindings FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation();
