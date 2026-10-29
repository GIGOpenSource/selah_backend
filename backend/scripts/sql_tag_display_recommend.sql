-- Selah CMS: tag library / display-structure / recommend-strategies
-- PostgreSQL. Idempotent where possible (IF NOT EXISTS).
-- Use this instead of: makemigrations models && migrate models
--
-- After running, restart API. First GET /api/admin-ops/tag-types/ will seed defaults.

BEGIN;

-- ---------- Wallpaper extra taxonomy columns ----------
ALTER TABLE t_wallpaper ADD COLUMN IF NOT EXISTS name_en varchar(120) NOT NULL DEFAULT '';
ALTER TABLE t_wallpaper ADD COLUMN IF NOT EXISTS name_es varchar(120) NOT NULL DEFAULT '';
ALTER TABLE t_wallpaper ADD COLUMN IF NOT EXISTS name_pt varchar(120) NOT NULL DEFAULT '';
ALTER TABLE t_wallpaper ADD COLUMN IF NOT EXISTS theme varchar(80) NOT NULL DEFAULT '';
ALTER TABLE t_wallpaper ADD COLUMN IF NOT EXISTS visual_tags jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE t_wallpaper ADD COLUMN IF NOT EXISTS scenario_tags jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE t_wallpaper ADD COLUMN IF NOT EXISTS emotion_tags jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE t_wallpaper ADD COLUMN IF NOT EXISTS taxonomy jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE t_wallpaper ADD COLUMN IF NOT EXISTS display_tags jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE t_wallpaper ADD COLUMN IF NOT EXISTS tag_index jsonb NOT NULL DEFAULT '[]'::jsonb;

-- Quote already has content_type/theme/scenario_tags/emotion_tags/taxonomy/display_tags/tag_index/pool_rank_score
-- on current branch; leave as-is.

-- ---------- Recommend strategies ----------
CREATE TABLE IF NOT EXISTS t_recommend_strategy (
    id bigserial PRIMARY KEY,
    name varchar(100) NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    priority integer NOT NULL DEFAULT 0,
    is_default boolean NOT NULL DEFAULT false,
    conditions jsonb NOT NULL DEFAULT '[]'::jsonb,
    action jsonb NOT NULL DEFAULT '{}'::jsonb,
    app_key varchar(32) NOT NULL DEFAULT '',
    language varchar(16) NOT NULL DEFAULT '',
    platform varchar(16) NOT NULL DEFAULT '',
    remark varchar(255) NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS rec_stg_scope_idx
    ON t_recommend_strategy (enabled, app_key, language, platform);

-- ---------- Quote daily engage (pool rank) ----------
CREATE TABLE IF NOT EXISTS t_quote_daily_engage (
    id bigserial PRIMARY KEY,
    quote_id integer NOT NULL,
    day date NOT NULL,
    expose_count integer NOT NULL DEFAULT 0,
    like_count integer NOT NULL DEFAULT 0,
    share_count integer NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT t_quote_daily_engage_quote_id_day_uniq UNIQUE (quote_id, day)
);
CREATE INDEX IF NOT EXISTS t_quote_daily_engage_quote_id_idx ON t_quote_daily_engage (quote_id);
CREATE INDEX IF NOT EXISTS quote_engage_day_idx ON t_quote_daily_engage (day);

-- ---------- Tag types ----------
CREATE TABLE IF NOT EXISTS t_tag_type (
    id bigserial PRIMARY KEY,
    key varchar(64) NOT NULL,
    label varchar(80) NOT NULL,
    prefix varchar(8) NOT NULL DEFAULT 'L',
    scope varchar(16) NOT NULL DEFAULT 'both',
    required boolean NOT NULL DEFAULT false,
    locked boolean NOT NULL DEFAULT false,
    sort_order integer NOT NULL DEFAULT 0,
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT t_tag_type_key_key UNIQUE (key)
);

-- ---------- Tags ----------
CREATE TABLE IF NOT EXISTS t_tag (
    id bigserial PRIMARY KEY,
    type_key varchar(64) NOT NULL,
    code varchar(16) NOT NULL DEFAULT '',
    name varchar(80) NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    related_themes jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT t_tag_type_key_name_uniq UNIQUE (type_key, name)
);
CREATE INDEX IF NOT EXISTS t_tag_type_key_idx ON t_tag (type_key);
CREATE INDEX IF NOT EXISTS tag_type_enabled_idx ON t_tag (type_key, enabled);

-- ---------- Display tags (edit state) ----------
CREATE TABLE IF NOT EXISTS t_display_tag (
    id bigserial PRIMARY KEY,
    kind varchar(16) NOT NULL,
    display_type varchar(24) NOT NULL DEFAULT 'collection',
    parent_id integer NULL,
    source_tag_id integer NULL,
    name_en varchar(80) NOT NULL,
    name_es varchar(80) NOT NULL DEFAULT '',
    name_pt varchar(80) NOT NULL DEFAULT '',
    sort integer NOT NULL DEFAULT 1,
    status integer NOT NULL DEFAULT 0,
    vip boolean NOT NULL DEFAULT false,
    mode varchar(16) NOT NULL DEFAULT 'auto',
    match varchar(8) NOT NULL DEFAULT 'any',
    conditions jsonb NOT NULL DEFAULT '[]'::jsonb,
    manual_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS display_tag_kind_idx ON t_display_tag (kind, status, sort);
CREATE INDEX IF NOT EXISTS display_tag_type_idx ON t_display_tag (kind, display_type);

-- ---------- Display structure publish state ----------
CREATE TABLE IF NOT EXISTS t_display_structure (
    id bigserial PRIMARY KEY,
    kind varchar(16) NOT NULL,
    dirty boolean NOT NULL DEFAULT false,
    published_version integer NOT NULL DEFAULT 0,
    wallpaper_filters jsonb NOT NULL DEFAULT '{}'::jsonb,
    wallpaper_filters_published jsonb NOT NULL DEFAULT '{}'::jsonb,
    published_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    published_by varchar(80) NOT NULL DEFAULT '',
    published_at timestamptz NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT t_display_structure_kind_key UNIQUE (kind)
);

COMMIT;
