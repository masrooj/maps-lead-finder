-- Ranking modes CRUD, demo media library, payment settings already use app_settings
CREATE TABLE IF NOT EXISTS contact_queue_modes (
    id INTEGER PRIMARY KEY,
    slug VARCHAR(80) NOT NULL UNIQUE,
    name VARCHAR(200) NOT NULL DEFAULT '',
    description TEXT DEFAULT '',
    filter_json TEXT DEFAULT '{}',
    boost_json TEXT DEFAULT '{}',
    sort_order INTEGER DEFAULT 0,
    active BOOLEAN DEFAULT 1,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS demo_assets (
    id INTEGER PRIMARY KEY,
    title VARCHAR(300) NOT NULL DEFAULT '',
    kind VARCHAR(40) NOT NULL DEFAULT 'link',
    url TEXT DEFAULT '',
    file_path TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    active BOOLEAN DEFAULT 1,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_contact_queue_modes_active ON contact_queue_modes (active);
CREATE INDEX IF NOT EXISTS ix_demo_assets_active ON demo_assets (active);
