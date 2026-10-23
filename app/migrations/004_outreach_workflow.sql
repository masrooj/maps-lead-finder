-- Mark-for-contact, archive, follow-ups, and outreach timestamps
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS marked_for_contact BOOLEAN DEFAULT FALSE;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS archived BOOLEAN DEFAULT FALSE;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS next_follow_up_at TIMESTAMP;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS last_contacted_at TIMESTAMP;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS outcome VARCHAR(80) DEFAULT '';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS search_labels VARCHAR(500) DEFAULT '';
