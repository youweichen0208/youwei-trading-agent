-- Preserve v1 payloads and request hashes; append v2 records to the same revision chain.
ALTER TABLE yta.model_reviews ADD COLUMN quality_gate_id text REFERENCES yta.model_quality_gates(id);
ALTER TABLE yta.model_reviews DROP CONSTRAINT model_reviews_payload_check;
ALTER TABLE yta.model_reviews DROP CONSTRAINT model_reviews_check6;
ALTER TABLE yta.model_reviews ADD CONSTRAINT review_evidence_version CHECK ((
  (payload->>'schema_version' = 'model-review-record-v1'
   AND quality_gate_id IS NULL AND NOT (payload ? 'quality_gate_id')
   AND payload->>'assessment_id' = assessment_id)
  OR
  (payload->>'schema_version' = 'model-review-record-v2'
   AND quality_gate_id IS NOT NULL AND payload->>'quality_gate_id' = quality_gate_id
   AND payload->>'price_assessment_id' = assessment_id)
) IS TRUE);
