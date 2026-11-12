-- A receipt is written only by the original executor after its awaited work
-- returns (QuantRunner waits for the subprocess close event). Timeouts alone
-- never insert a receipt and never authorize releasing an unknown operation.
ALTER TABLE tool_operations ADD UNIQUE(id,task_id,owner_id);
CREATE TABLE tool_exit_receipts (
 operation_id uuid PRIMARY KEY, task_id uuid NOT NULL, owner_id uuid NOT NULL,
 status text NOT NULL CHECK(status IN ('completed','failed','cancelled')),
 result jsonb, error_code text, exited_at timestamptz NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(operation_id,task_id,owner_id) REFERENCES tool_operations(id,task_id,owner_id)
);
CREATE TABLE execution_audit (
 sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 owner_id uuid NOT NULL, task_id uuid NOT NULL, operation_id uuid,
 event text NOT NULL, previous_status text, status text NOT NULL,
 reason text, generation integer NOT NULL, receipt_id uuid REFERENCES tool_exit_receipts(operation_id),
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(task_id,owner_id) REFERENCES tasks(id,owner_id),
 FOREIGN KEY(operation_id,task_id,owner_id) REFERENCES tool_operations(id,task_id,owner_id)
);
CREATE INDEX execution_audit_task ON execution_audit(owner_id,task_id,sequence);
CREATE FUNCTION reject_execution_evidence_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'immutable_execution_evidence'; END $$;
CREATE TRIGGER immutable_exit_receipts BEFORE UPDATE OR DELETE ON tool_exit_receipts
 FOR EACH ROW EXECUTE FUNCTION reject_execution_evidence_mutation();
CREATE TRIGGER immutable_exit_receipts_truncate BEFORE TRUNCATE ON tool_exit_receipts
 FOR EACH STATEMENT EXECUTE FUNCTION reject_execution_evidence_mutation();
CREATE TRIGGER immutable_execution_audit BEFORE UPDATE OR DELETE ON execution_audit
 FOR EACH ROW EXECUTE FUNCTION reject_execution_evidence_mutation();
CREATE TRIGGER immutable_execution_audit_truncate BEFORE TRUNCATE ON execution_audit
 FOR EACH STATEMENT EXECUTE FUNCTION reject_execution_evidence_mutation();

-- Audit only controlled state and identifiers, never prompts, capabilities,
-- credentials or provider error text. Records outlive SSE event retention.
CREATE FUNCTION audit_task_transition() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='INSERT' THEN
  INSERT INTO execution_audit(owner_id,task_id,event,status,generation)
   VALUES(NEW.owner_id,NEW.id,'task.created',NEW.status,NEW.generation);
 ELSIF ROW(OLD.status,OLD.cancel_reason,OLD.failure_code) IS DISTINCT FROM
       ROW(NEW.status,NEW.cancel_reason,NEW.failure_code) THEN
  INSERT INTO execution_audit(owner_id,task_id,event,previous_status,status,reason,generation)
   VALUES(NEW.owner_id,NEW.id,'task.transition',OLD.status,NEW.status,
          coalesce(NEW.cancel_reason,NEW.failure_code,NEW.error_code),NEW.generation);
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER audit_task_state AFTER INSERT OR UPDATE ON tasks
 FOR EACH ROW EXECUTE FUNCTION audit_task_transition();
CREATE FUNCTION audit_tool_transition() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE prior text; event_name text;
BEGIN
 IF TG_OP='INSERT' THEN event_name := 'tool.started';
 ELSIF ROW(OLD.status,OLD.error_code) IS DISTINCT FROM ROW(NEW.status,NEW.error_code) THEN
  prior := OLD.status; event_name := 'tool.transition';
 ELSE RETURN NEW;
 END IF;
 INSERT INTO execution_audit(owner_id,task_id,operation_id,event,previous_status,status,reason,generation,receipt_id)
  SELECT NEW.owner_id,NEW.task_id,NEW.id,event_name,prior,NEW.status,NEW.error_code,t.generation,r.operation_id
  FROM tasks t LEFT JOIN tool_exit_receipts r ON r.operation_id=NEW.id WHERE t.id=NEW.task_id;
 RETURN NEW;
END $$;
CREATE TRIGGER audit_tool_state AFTER INSERT OR UPDATE ON tool_operations
 FOR EACH ROW EXECUTE FUNCTION audit_tool_transition();
