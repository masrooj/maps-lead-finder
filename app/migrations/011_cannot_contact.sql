-- Proper cannot-contact flags for Contact queue (web + mobile).
-- via: whatsapp | instagram | both
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS cannot_contact BOOLEAN DEFAULT FALSE;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS cannot_contact_via VARCHAR(40) DEFAULT '';

CREATE INDEX IF NOT EXISTS ix_businesses_cannot_contact ON businesses (cannot_contact)
  WHERE cannot_contact IS TRUE;
