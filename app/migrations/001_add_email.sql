-- Email addresses are collected from a business website. Google Places does not provide them.
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS email VARCHAR(320) DEFAULT '';
