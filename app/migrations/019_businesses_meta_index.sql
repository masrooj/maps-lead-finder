-- Speed contact-queue/meta and stats aggregates keyed by product line + freshness
CREATE INDEX IF NOT EXISTS ix_businesses_product_line_archived_updated
  ON businesses (product_line, archived, updated_at);
