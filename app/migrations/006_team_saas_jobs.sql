-- Team territories, SaaS deal fields, job ticks support (additive)

CREATE TABLE IF NOT EXISTS sales_territories (
  id INTEGER PRIMARY KEY,
  name VARCHAR(200) DEFAULT '',
  country VARCHAR(120) DEFAULT '',
  region VARCHAR(120) DEFAULT '',
  owner_id INTEGER,
  active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMP
);

ALTER TABLE businesses ADD COLUMN IF NOT EXISTS mrr FLOAT;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS trial_starts_at TIMESTAMP;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMP;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS saas_plan_code VARCHAR(80) DEFAULT '';
