CREATE INDEX audit_acquisition_attempt_lookup ON yta.audit_events(resource_id, id)
WHERE action IN ('provider.acquire_started','provider.acquire_completed','provider.acquire_failed');
