-- AppointHive Sales OS foundation (additive)

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  email VARCHAR(320) NOT NULL UNIQUE,
  name VARCHAR(200) DEFAULT '',
  role VARCHAR(40) DEFAULT 'owner',
  timezone VARCHAR(80) DEFAULT 'Australia/Sydney',
  active BOOLEAN DEFAULT TRUE,
  password_hash VARCHAR(200) DEFAULT '',
  created_at TIMESTAMP,
  updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pipeline_stages (
  id INTEGER PRIMARY KEY,
  product_line VARCHAR(40) NOT NULL,
  slug VARCHAR(80) NOT NULL,
  name VARCHAR(120) NOT NULL,
  sort_order INTEGER DEFAULT 0,
  probability FLOAT DEFAULT 0,
  is_terminal BOOLEAN DEFAULT FALSE,
  is_won BOOLEAN DEFAULT FALSE,
  is_lost BOOLEAN DEFAULT FALSE,
  active BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS stage_history (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL,
  from_stage_id INTEGER,
  to_stage_id INTEGER NOT NULL,
  changed_by INTEGER,
  note TEXT DEFAULT '',
  created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL,
  name VARCHAR(200) DEFAULT '',
  role VARCHAR(120) DEFAULT '',
  email VARCHAR(320) DEFAULT '',
  phone VARCHAR(100) DEFAULT '',
  preferred_channel VARCHAR(40) DEFAULT '',
  verified BOOLEAN DEFAULT FALSE,
  notes TEXT DEFAULT '',
  created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS activities (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL,
  contact_id INTEGER,
  activity_type VARCHAR(80) NOT NULL,
  summary VARCHAR(500) DEFAULT '',
  outcome VARCHAR(500) DEFAULT '',
  next_action VARCHAR(500) DEFAULT '',
  channel VARCHAR(40) DEFAULT '',
  created_by INTEGER,
  created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY,
  business_id INTEGER,
  title VARCHAR(300) DEFAULT '',
  description TEXT DEFAULT '',
  priority VARCHAR(40) DEFAULT 'normal',
  status VARCHAR(40) DEFAULT 'open',
  due_at TIMESTAMP,
  completed_at TIMESTAMP,
  owner_id INTEGER,
  created_at TIMESTAMP,
  updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_settings (
  id INTEGER PRIMARY KEY,
  key VARCHAR(120) NOT NULL UNIQUE,
  value TEXT DEFAULT '',
  updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS plans (
  id INTEGER PRIMARY KEY,
  product_line VARCHAR(40) NOT NULL,
  code VARCHAR(80) NOT NULL UNIQUE,
  name VARCHAR(200) NOT NULL,
  description TEXT DEFAULT '',
  sort_order INTEGER DEFAULT 0,
  active BOOLEAN DEFAULT TRUE,
  billing_period VARCHAR(40) DEFAULT 'ONE_TIME',
  created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS plan_prices (
  id INTEGER PRIMARY KEY,
  plan_id INTEGER NOT NULL,
  currency VARCHAR(10) DEFAULT 'AUD',
  amount FLOAT DEFAULT 0,
  valid_from TIMESTAMP,
  valid_to TIMESTAMP
);

CREATE TABLE IF NOT EXISTS plan_features (
  id INTEGER PRIMARY KEY,
  plan_id INTEGER NOT NULL,
  feature_key VARCHAR(120) NOT NULL,
  label VARCHAR(300) DEFAULT '',
  included BOOLEAN DEFAULT TRUE,
  limit_value INTEGER
);

CREATE TABLE IF NOT EXISTS proposals (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL,
  title VARCHAR(300) DEFAULT '',
  reference VARCHAR(80) NOT NULL UNIQUE,
  status VARCHAR(40) DEFAULT 'draft',
  currency VARCHAR(10) DEFAULT 'AUD',
  subtotal FLOAT DEFAULT 0,
  discount_amount FLOAT DEFAULT 0,
  discount_reason VARCHAR(500) DEFAULT '',
  tax_rate FLOAT DEFAULT 0,
  tax_label VARCHAR(80) DEFAULT '',
  tax_amount FLOAT DEFAULT 0,
  total FLOAT DEFAULT 0,
  plan_id INTEGER,
  line_items_json TEXT DEFAULT '[]',
  version INTEGER DEFAULT 1,
  sent_at TIMESTAMP,
  expires_at TIMESTAMP,
  accepted_at TIMESTAMP,
  terms_url VARCHAR(500) DEFAULT '',
  terms_accepted BOOLEAN DEFAULT FALSE,
  notes TEXT DEFAULT '',
  created_at TIMESTAMP,
  updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS billing_entities (
  id INTEGER PRIMARY KEY,
  code VARCHAR(80) NOT NULL UNIQUE,
  legal_name VARCHAR(300) NOT NULL,
  address TEXT DEFAULT '',
  country VARCHAR(8) DEFAULT '',
  tax_id VARCHAR(120) DEFAULT '',
  active BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS invoices (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL,
  proposal_id INTEGER,
  billing_entity_id INTEGER,
  number VARCHAR(80) NOT NULL UNIQUE,
  status VARCHAR(40) DEFAULT 'draft',
  currency VARCHAR(10) DEFAULT 'AUD',
  subtotal FLOAT DEFAULT 0,
  tax_amount FLOAT DEFAULT 0,
  total FLOAT DEFAULT 0,
  provider VARCHAR(40) DEFAULT '',
  provider_ref VARCHAR(200) DEFAULT '',
  hosted_url TEXT DEFAULT '',
  pay_instructions TEXT DEFAULT '',
  line_items_json TEXT DEFAULT '[]',
  expires_at TIMESTAMP,
  paid_at TIMESTAMP,
  is_test_mode BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMP,
  updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS payments (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL,
  invoice_id INTEGER,
  amount FLOAT DEFAULT 0,
  currency VARCHAR(10) DEFAULT 'AUD',
  status VARCHAR(40) DEFAULT 'succeeded',
  provider VARCHAR(40) DEFAULT 'manual',
  method VARCHAR(80) DEFAULT '',
  provider_ref VARCHAR(200) DEFAULT '',
  paid_at TIMESTAMP,
  is_test_mode BOOLEAN DEFAULT FALSE,
  notes TEXT DEFAULT '',
  created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS webhook_events (
  id INTEGER PRIMARY KEY,
  provider VARCHAR(40) NOT NULL,
  event_id VARCHAR(200) NOT NULL UNIQUE,
  payload_json TEXT DEFAULT '{}',
  processed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS compliance_packs (
  id INTEGER PRIMARY KEY,
  country_code VARCHAR(8) NOT NULL UNIQUE,
  title VARCHAR(200) DEFAULT '',
  bullets_md TEXT DEFAULT '',
  required_fields_json TEXT DEFAULT '[]',
  enforce BOOLEAN DEFAULT FALSE,
  counsel_reviewed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS suppressions (
  id INTEGER PRIMARY KEY,
  kind VARCHAR(40) NOT NULL,
  value VARCHAR(320) NOT NULL,
  reason VARCHAR(500) DEFAULT '',
  created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  job_type VARCHAR(80) NOT NULL,
  status VARCHAR(40) DEFAULT 'pending',
  cursor_json TEXT DEFAULT '{}',
  payload_json TEXT DEFAULT '{}',
  error TEXT DEFAULT '',
  created_at TIMESTAMP,
  updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY,
  actor_id INTEGER,
  action VARCHAR(120) NOT NULL,
  entity_type VARCHAR(80) DEFAULT '',
  entity_id INTEGER,
  detail_json TEXT DEFAULT '{}',
  created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS discovery_notes (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL,
  existing_system TEXT DEFAULT '',
  current_workflow TEXT DEFAULT '',
  painful_issue TEXT DEFAULT '',
  frequency_impact TEXT DEFAULT '',
  desired_outcome TEXT DEFAULT '',
  buying_authority TEXT DEFAULT '',
  purchase_timing TEXT DEFAULT '',
  switching_concerns TEXT DEFAULT '',
  required_capabilities TEXT DEFAULT '',
  call_notes TEXT DEFAULT '',
  next_action VARCHAR(500) DEFAULT '',
  created_at TIMESTAMP,
  updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS demo_notes (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL,
  demo_at TIMESTAMP,
  outcome VARCHAR(200) DEFAULT '',
  features_demonstrated TEXT DEFAULT '',
  prospect_reaction TEXT DEFAULT '',
  unresolved_concerns TEXT DEFAULT '',
  product_gaps TEXT DEFAULT '',
  agreed_next_step TEXT DEFAULT '',
  call_notes TEXT DEFAULT '',
  created_at TIMESTAMP,
  updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS saved_views (
  id INTEGER PRIMARY KEY,
  user_id INTEGER,
  name VARCHAR(200) NOT NULL,
  filters_json TEXT DEFAULT '{}',
  created_at TIMESTAMP
);

ALTER TABLE businesses ADD COLUMN IF NOT EXISTS product_line VARCHAR(40) DEFAULT 'STANDALONE';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS pipeline_stage_id INTEGER;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS owner_id INTEGER;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS state VARCHAR(120) DEFAULT '';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS postcode VARCHAR(40) DEFAULT '';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS timezone VARCHAR(80) DEFAULT '';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS deal_value FLOAT;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS currency VARCHAR(10) DEFAULT 'AUD';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS estimated_close_date TIMESTAMP;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS next_action VARCHAR(500) DEFAULT '';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS lead_source VARCHAR(120) DEFAULT '';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS date_identified TIMESTAMP;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS last_verified_at TIMESTAMP;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS estimated_staff INTEGER;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS locations_count INTEGER;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS current_booking_software VARCHAR(200) DEFAULT '';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS main_pain TEXT DEFAULT '';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS buying_trigger TEXT DEFAULT '';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS objections TEXT DEFAULT '';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS lost_reason VARCHAR(500) DEFAULT '';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS qualification_score INTEGER;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS score_override INTEGER;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS score_override_reason VARCHAR(500) DEFAULT '';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS score_criteria_json TEXT DEFAULT '{}';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS scores_verified_at TIMESTAMP;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS opt_out BOOLEAN DEFAULT FALSE;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS opt_out_channels VARCHAR(200) DEFAULT '';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS permission_status_json TEXT DEFAULT '{}';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS tax_id VARCHAR(120) DEFAULT '';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS billing_entity_name VARCHAR(300) DEFAULT '';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS billing_address TEXT DEFAULT '';
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS linked_prospect_id INTEGER;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS plan_id INTEGER;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS is_demo_data BOOLEAN DEFAULT FALSE;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS attachment_links_json TEXT DEFAULT '[]';

ALTER TABLE message_templates ADD COLUMN IF NOT EXISTS product_line VARCHAR(40) DEFAULT '';
ALTER TABLE message_templates ADD COLUMN IF NOT EXISTS country VARCHAR(8) DEFAULT '';
ALTER TABLE message_templates ADD COLUMN IF NOT EXISTS purpose VARCHAR(80) DEFAULT '';
ALTER TABLE message_templates ADD COLUMN IF NOT EXISTS governance_ok BOOLEAN DEFAULT TRUE;
