-- 灵修课时经文正文独立字段（与 models.DevotionSession.scripture_text 对齐）。
-- 在目标库执行一次（PostgreSQL）。也可：manage.py migrate models

ALTER TABLE t_devotion_session
  ADD COLUMN IF NOT EXISTS scripture_text text NOT NULL DEFAULT '';
