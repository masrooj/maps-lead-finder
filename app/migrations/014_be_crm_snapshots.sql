-- AppointHive CRM snapshots: event inbox, commercial fields, read-only milestones
ALTER TABLE be_deliveries ADD COLUMN IF NOT EXISTS customer_ref VARCHAR(120) DEFAULT '';
ALTER TABLE be_deliveries ADD COLUMN IF NOT EXISTS package_name VARCHAR(200) DEFAULT '';
ALTER TABLE be_deliveries ADD COLUMN IF NOT EXISTS order_type VARCHAR(40) DEFAULT '';
ALTER TABLE be_deliveries ADD COLUMN IF NOT EXISTS quote_status VARCHAR(40) DEFAULT '';
ALTER TABLE be_deliveries ADD COLUMN IF NOT EXISTS agreement_status VARCHAR(40) DEFAULT '';
ALTER TABLE be_deliveries ADD COLUMN IF NOT EXISTS invoice_status VARCHAR(40) DEFAULT '';
ALTER TABLE be_deliveries ADD COLUMN IF NOT EXISTS financial_status VARCHAR(40) DEFAULT '';
ALTER TABLE be_deliveries ADD COLUMN IF NOT EXISTS delivery_status VARCHAR(40) DEFAULT '';
ALTER TABLE be_deliveries ADD COLUMN IF NOT EXISTS upgrade_status VARCHAR(40) DEFAULT '';
ALTER TABLE be_deliveries ADD COLUMN IF NOT EXISTS paid_amount_cents INTEGER DEFAULT 0;
ALTER TABLE be_deliveries ADD COLUMN IF NOT EXISTS outstanding_cents INTEGER DEFAULT 0;
ALTER TABLE be_deliveries ADD COLUMN IF NOT EXISTS release_status VARCHAR(40) DEFAULT '';
ALTER TABLE be_deliveries ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMP WITH TIME ZONE;

CREATE TABLE IF NOT EXISTS be_event_inbox (
    id INTEGER PRIMARY KEY,
    event_id VARCHAR(200) NOT NULL,
    event_type VARCHAR(120) NOT NULL DEFAULT '',
    event_version INTEGER NOT NULL DEFAULT 1,
    status VARCHAR(40) NOT NULL DEFAULT 'processed',
    business_id INTEGER,
    delivery_id INTEGER,
    payload_json TEXT DEFAULT '{}',
    error TEXT DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 1,
    processed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_be_event_inbox_event_id ON be_event_inbox (event_id);
CREATE INDEX IF NOT EXISTS ix_be_event_inbox_status ON be_event_inbox (status);
CREATE INDEX IF NOT EXISTS ix_be_event_inbox_type ON be_event_inbox (event_type);
CREATE INDEX IF NOT EXISTS ix_be_event_inbox_business ON be_event_inbox (business_id);
CREATE INDEX IF NOT EXISTS ix_be_event_inbox_created ON be_event_inbox (created_at);

CREATE TABLE IF NOT EXISTS be_milestone_snapshots (
    id INTEGER PRIMARY KEY,
    delivery_id INTEGER NOT NULL,
    business_id INTEGER NOT NULL,
    ah_milestone_id VARCHAR(120) NOT NULL DEFAULT '',
    name VARCHAR(200) DEFAULT '',
    amount_cents INTEGER DEFAULT 0,
    currency VARCHAR(10) DEFAULT 'USD',
    due_at TIMESTAMP WITH TIME ZONE,
    status VARCHAR(40) DEFAULT 'pending',
    pay_url TEXT DEFAULT '',
    last_synced_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_be_milestones_delivery ON be_milestone_snapshots (delivery_id);
CREATE INDEX IF NOT EXISTS ix_be_milestones_business ON be_milestone_snapshots (business_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_be_milestones_ah ON be_milestone_snapshots (delivery_id, ah_milestone_id);
