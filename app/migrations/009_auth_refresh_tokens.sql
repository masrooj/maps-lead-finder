-- Mobile / Bearer refresh tokens (opaque; only SHA-256 hash stored)
CREATE TABLE IF NOT EXISTS auth_refresh_tokens (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    token_hash VARCHAR(64) NOT NULL,
    family_id VARCHAR(64) NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    revoked_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP WITH TIME ZONE,
    user_agent VARCHAR(500) DEFAULT '',
    client VARCHAR(40) DEFAULT 'mobile'
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_auth_refresh_token_hash ON auth_refresh_tokens (token_hash);
CREATE INDEX IF NOT EXISTS ix_auth_refresh_user ON auth_refresh_tokens (user_id);
CREATE INDEX IF NOT EXISTS ix_auth_refresh_family ON auth_refresh_tokens (family_id);
