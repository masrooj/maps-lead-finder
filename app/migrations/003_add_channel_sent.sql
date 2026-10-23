-- Track which outreach channels have already been messaged for each lead.
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS email_sent BOOLEAN DEFAULT FALSE;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS whatsapp_sent BOOLEAN DEFAULT FALSE;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS instagram_sent BOOLEAN DEFAULT FALSE;
