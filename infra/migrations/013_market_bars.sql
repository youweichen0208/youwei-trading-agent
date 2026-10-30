-- Derived candidate daily records. Qualification remains explicitly unverified.
-- Provider/symbol/mode and availability are obtained through the immutable evidence FK.
CREATE TABLE yta.market_bars (
  acquisition_hash text NOT NULL,
  kind text NOT NULL DEFAULT 'bars' CHECK (kind = 'bars'),
  page_index integer NOT NULL,
  row_index integer NOT NULL CHECK (row_index >= 0),
  session_date date NOT NULL,
  aggregate_timestamp_ms bigint NOT NULL,
  open numeric NOT NULL CHECK (open > 0 AND open < 'Infinity'::numeric),
  high numeric NOT NULL CHECK (high > 0 AND high < 'Infinity'::numeric),
  low numeric NOT NULL CHECK (low > 0 AND low < 'Infinity'::numeric),
  close numeric NOT NULL CHECK (close > 0 AND close < 'Infinity'::numeric),
  volume numeric NOT NULL CHECK (volume >= 0 AND volume < 'Infinity'::numeric),
  price_basis text NOT NULL DEFAULT 'RAW' CHECK (price_basis = 'RAW'),
  qualification_status text NOT NULL DEFAULT 'UNVERIFIED' CHECK (qualification_status = 'UNVERIFIED'),
  PRIMARY KEY (acquisition_hash, session_date),
  UNIQUE (acquisition_hash, page_index, row_index),
  FOREIGN KEY (acquisition_hash, kind, page_index)
    REFERENCES yta.provider_response_pages(acquisition_hash, kind, page_index),
  CHECK (low <= open AND open <= high AND low <= close AND close <= high)
);
CREATE INDEX market_bars_session ON yta.market_bars(session_date, acquisition_hash);
CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.market_bars
  FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation();
