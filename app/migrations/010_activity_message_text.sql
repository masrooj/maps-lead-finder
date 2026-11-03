-- Store full sent outreach bodies on activities (was VARCHAR 500).
ALTER TABLE activities ALTER COLUMN next_action TYPE TEXT;
ALTER TABLE activities ALTER COLUMN summary TYPE TEXT;
