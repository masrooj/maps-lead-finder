-- Contact workflow split: To contact (prep) vs Ready to contact (messaging)
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS ready_for_contact BOOLEAN DEFAULT FALSE;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS outreach_channel VARCHAR(40) DEFAULT '';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS follow_up_kind VARCHAR(40) DEFAULT '';
