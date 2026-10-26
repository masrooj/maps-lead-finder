-- AppointHive Booking Engine bridge (Plan 2)
CREATE TABLE IF NOT EXISTS be_deliveries (
    id INTEGER PRIMARY KEY,
    business_id INTEGER NOT NULL,
    proposal_id INTEGER,
    purpose VARCHAR(40) NOT NULL DEFAULT 'purchase',
    purchase_id VARCHAR(120) DEFAULT '',
    inbound_request_id VARCHAR(120) DEFAULT '',
    revision_id VARCHAR(120) DEFAULT '',
    from_revision_id VARCHAR(120) DEFAULT '',
    installation_id VARCHAR(120) DEFAULT '',
    pay_url TEXT DEFAULT '',
    quoted_amount_cents INTEGER DEFAULT 0,
    currency VARCHAR(10) DEFAULT 'AUD',
    crm_lead_id VARCHAR(120) DEFAULT '',
    status VARCHAR(40) NOT NULL DEFAULT 'sent',
    last_event_json TEXT DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_be_deliveries_business ON be_deliveries (business_id);
CREATE INDEX IF NOT EXISTS ix_be_deliveries_purchase ON be_deliveries (purchase_id);
CREATE INDEX IF NOT EXISTS ix_be_deliveries_request ON be_deliveries (inbound_request_id);
CREATE INDEX IF NOT EXISTS ix_be_deliveries_status ON be_deliveries (status);
CREATE INDEX IF NOT EXISTS ix_be_deliveries_crm_lead ON be_deliveries (crm_lead_id);

-- Optional installation id on business for upgrade path
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS be_installation_id VARCHAR(120) DEFAULT '';
