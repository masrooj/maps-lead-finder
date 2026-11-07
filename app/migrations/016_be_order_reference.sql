-- AppointHive order reference (AH orderReference) — display/match only, not a payment authority
ALTER TABLE be_deliveries ADD COLUMN IF NOT EXISTS order_reference VARCHAR(120) DEFAULT '';
CREATE INDEX IF NOT EXISTS ix_be_deliveries_order_reference ON be_deliveries (order_reference);
