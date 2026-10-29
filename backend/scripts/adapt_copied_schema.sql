-- After restoring a remote dump into local Docker Postgres:
-- 1) OAuth IDs unique per app (Q-004)
-- 2) Membership SKU unique includes app_key (Q-018)
-- 3) Fill empty app_key on Manna-only content so Q-017 does not hide it

-- Drop leftover global uniques on oauth ids (names vary by dump vintage)
ALTER TABLE t_user DROP CONSTRAINT IF EXISTS t_user_google_id_key;
ALTER TABLE t_user DROP CONSTRAINT IF EXISTS t_user_apple_id_key;
DROP INDEX IF EXISTS t_user_google_id_key;
DROP INDEX IF EXISTS t_user_apple_id_key;
DROP INDEX IF EXISTS t_user_google_id_app_key_uidx;
DROP INDEX IF EXISTS t_user_apple_id_app_key_uidx;

CREATE UNIQUE INDEX t_user_google_id_app_key_uidx
  ON t_user (google_id, app_key)
  WHERE google_id IS NOT NULL AND google_id <> '';

CREATE UNIQUE INDEX t_user_apple_id_app_key_uidx
  ON t_user (apple_id, app_key)
  WHERE apple_id IS NOT NULL AND apple_id <> '';

-- Membership SKU
ALTER TABLE t_membership_product
  DROP CONSTRAINT IF EXISTS t_membership_product_platform_store_product_id_uniq;
ALTER TABLE t_membership_product
  DROP CONSTRAINT IF EXISTS t_membership_product_platform_store_product_id_key;
ALTER TABLE t_membership_product
  DROP CONSTRAINT IF EXISTS t_membership_product_app_key_platform_store_product_id_uniq;

CREATE UNIQUE INDEX IF NOT EXISTS t_membership_product_app_plat_sku_uidx
  ON t_membership_product (app_key, platform, store_product_id);

-- Manna content: empty app_key is no longer visible to either App
UPDATE t_daily_verse SET app_key = 'manna' WHERE COALESCE(app_key, '') = '';
UPDATE t_prayer_content SET app_key = 'manna' WHERE COALESCE(app_key, '') = '';
UPDATE t_devotion_series SET app_key = 'manna' WHERE COALESCE(app_key, '') = '';
UPDATE t_manna_quiz_level SET app_key = 'manna' WHERE COALESCE(app_key, '') = '';
UPDATE t_manna_quiz_story SET app_key = 'manna' WHERE COALESCE(app_key, '') = '';
UPDATE t_badge_def SET app_key = 'manna' WHERE COALESCE(app_key, '') = '';
UPDATE t_manna_home_card SET app_key = 'manna' WHERE COALESCE(app_key, '') = '';
UPDATE t_manna_testimony SET app_key = 'manna' WHERE COALESCE(app_key, '') = '';
UPDATE t_manna_stats_copy SET app_key = 'manna' WHERE COALESCE(app_key, '') = '';
UPDATE t_manna_content_tag SET app_key = 'manna' WHERE COALESCE(app_key, '') = '';
UPDATE t_bible_translation SET app_key = 'manna' WHERE COALESCE(app_key, '') = '';

-- Amen / 读经发奖幂等：同一 user×app×reason×ref_id 只能一条（空 ref 不参与）
DROP INDEX IF EXISTS t_manna_ledger_user_reason_ref_uidx;
CREATE UNIQUE INDEX t_manna_ledger_user_reason_ref_uidx
  ON t_manna_ledger (user_id, app_key, reason, ref_id)
  WHERE ref_id IS NOT NULL AND ref_id <> '';
