-- Native event IDs are scoped to one execution, never browser SSE cursors.
ALTER TABLE executions ADD COLUMN event_cursor bigint NOT NULL DEFAULT -1
 CHECK(event_cursor BETWEEN -1 AND 9007199254740991);
ALTER TABLE executions ADD COLUMN event_gap boolean NOT NULL DEFAULT false;
ALTER TABLE executions ADD COLUMN event_closed boolean NOT NULL DEFAULT false;
ALTER TABLE tasks ADD COLUMN stream_text text NOT NULL DEFAULT ''
 CHECK(length(stream_text)<=500000);
ALTER TABLE tasks ADD COLUMN stream_message_id uuid NOT NULL DEFAULT gen_random_uuid();
