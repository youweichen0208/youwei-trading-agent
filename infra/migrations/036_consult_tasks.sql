-- Consultation tasks: a message that is not stock research still needs a task row
-- (messages.task_id is NOT NULL), but it must not run the research pipeline,
-- must not dispatch specialists, and must not consume research capacity.
ALTER TABLE tasks DROP CONSTRAINT tasks_role_check;
ALTER TABLE tasks ADD CONSTRAINT tasks_role_check
  CHECK (role IN ('research', 'prediction', 'portfolio', 'consult'));

-- tasks_check pinned every root task to role='research'. A consult is also a
-- root task, so the invariant becomes "roots are research or consult".
ALTER TABLE tasks DROP CONSTRAINT tasks_check;
ALTER TABLE tasks ADD CONSTRAINT tasks_check
  CHECK (
    (parent_id IS NULL AND root_id = id AND role IN ('research', 'consult'))
    OR (parent_id IS NOT NULL AND root_id = parent_id AND role <> 'research')
  );
