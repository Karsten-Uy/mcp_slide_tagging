-- Initial schema for the read-only slide-corpus MCP server.
--
-- Adapted from the design spec to match the real corpus shape:
--   * deck_type, role, and summary_text are NULLABLE (the corpus is hand-labeled
--     incrementally, so semantic fields are often null at ingest time).
--   * deck_id / slide_id are derived (UUIDv5) by the ingest script; the source
--     JSON carries neither.
--   * has_image is derived from image_count > 0.
--   * visual_embedding stays nullable and is populated only when a thumbnail exists.
--   * raw_json on both tables preserves the full original JSON so a later schema
--     change can be backfilled without re-reading source files.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS decks (
    deck_id UUID PRIMARY KEY,
    source_filename TEXT,
    deck_type TEXT,
    style_archetype TEXT,
    narrative_structure TEXT,
    dominant_visual_mode TEXT,
    design_system JSONB NOT NULL DEFAULT '{}'::jsonb,
    consistency_score FLOAT,
    summary_text TEXT,
    summary_embedding VECTOR(1536),
    slide_count INT,
    raw_json JSONB NOT NULL,
    tagged_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS slides (
    slide_id UUID PRIMARY KEY,
    deck_id UUID REFERENCES decks(deck_id) ON DELETE CASCADE,
    index INT NOT NULL,
    role TEXT,
    layout_archetype_id TEXT,
    density_bucket TEXT,
    word_count INT,
    text_blocks INT,
    visual_elements INT,
    whitespace_ratio FLOAT,
    has_chart BOOLEAN DEFAULT FALSE,
    has_image BOOLEAN DEFAULT FALSE,
    core_message TEXT,
    emphasis_techniques TEXT[],
    structural_data JSONB,
    thumbnail_path TEXT,
    core_message_embedding VECTOR(1536),
    visual_embedding VECTOR(768),
    raw_json JSONB NOT NULL,
    tagged_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_slides_role_density ON slides(role, density_bucket);
CREATE INDEX IF NOT EXISTS idx_slides_deck ON slides(deck_id);
CREATE INDEX IF NOT EXISTS idx_slides_message_emb ON slides USING ivfflat (core_message_embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_slides_visual_emb ON slides USING ivfflat (visual_embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_decks_summary_emb ON decks USING ivfflat (summary_embedding vector_cosine_ops) WITH (lists = 50);
