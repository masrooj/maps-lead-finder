-- Customer reference on prospect + outbound failure log for safe retry visibility
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS be_customer_ref VARCHAR(120) DEFAULT '';
CREATE INDEX IF NOT EXISTS ix_businesses_be_customer_ref ON businesses (be_customer_ref);

CREATE TABLE IF NOT EXISTS be_outbound_failures (
    id INTEGER PRIMARY KEY,
    business_id INTEGER,
    operation VARCHAR(80) NOT NULL DEFAULT '',
    error TEXT DEFAULT '',
    payload_json TEXT DEFAULT '{}',
    status VARCHAR(40) NOT NULL DEFAULT 'failed',
    attempts INTEGER NOT NULL DEFAULT 1,
    last_attempt_at TIMESTAMP WITH TIME ZONE,
    resolved_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_be_outbound_failures_status ON be_outbound_failures (status);
CREATE INDEX IF NOT EXISTS ix_be_outbound_failures_business ON be_outbound_failures (business_id);
