-- Already connected park (shared IG/WA/email, other location) + IG awaiting follow-back hold
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS already_connected BOOLEAN DEFAULT FALSE;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS already_connected_primary_id INTEGER NULL;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS ig_awaiting_follow_back BOOLEAN DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS ix_businesses_already_connected ON businesses (already_connected)
  WHERE already_connected IS TRUE;
CREATE INDEX IF NOT EXISTS ix_businesses_already_connected_primary
  ON businesses (already_connected_primary_id)
  WHERE already_connected_primary_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_businesses_ig_awaiting_follow_back ON businesses (ig_awaiting_follow_back)
  WHERE ig_awaiting_follow_back IS TRUE;
