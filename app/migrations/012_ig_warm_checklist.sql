-- Instagram warm checklist (follow / like / comment) before soft DM confirm-sent.
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS ig_warm_follow BOOLEAN DEFAULT FALSE;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS ig_warm_like BOOLEAN DEFAULT FALSE;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS ig_warm_comment BOOLEAN DEFAULT FALSE;
