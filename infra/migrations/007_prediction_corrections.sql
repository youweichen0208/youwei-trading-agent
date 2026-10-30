ALTER TABLE yta.predictions ADD COLUMN supersedes_prediction_id uuid;
ALTER TABLE yta.predictions ADD CONSTRAINT predictions_owner_mode_identity
  UNIQUE (id, owner_id, data_mode);
ALTER TABLE yta.predictions ADD CONSTRAINT prediction_correction_owner_mode
  FOREIGN KEY (supersedes_prediction_id, owner_id, data_mode)
  REFERENCES yta.predictions(id, owner_id, data_mode);
ALTER TABLE yta.predictions ADD CONSTRAINT prediction_correction_not_self
  CHECK (supersedes_prediction_id IS NULL OR supersedes_prediction_id <> id);
CREATE INDEX predictions_correction_source ON yta.predictions(supersedes_prediction_id)
  WHERE supersedes_prediction_id IS NOT NULL;
