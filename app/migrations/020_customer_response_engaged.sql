-- Persist customer reply text when marking Engaged (Contact queue Engaged tab)
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS customer_response TEXT DEFAULT '';
