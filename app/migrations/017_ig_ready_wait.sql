-- IG Ready wait: soft ask unlocks N Settings-timezone days after promote to Ready
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS ig_ready_at TIMESTAMPTZ NULL;
