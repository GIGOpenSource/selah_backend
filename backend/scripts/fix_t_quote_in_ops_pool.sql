-- t_quote：代码字段 in_cold_start_pool 对应物理列 in_ops_pool（历史名）。
-- 若曾误加 in_cold_start_pool 列，把数据合并回 in_ops_pool 后可选删除冗余列。

ALTER TABLE t_quote ALTER COLUMN in_ops_pool SET DEFAULT false;
UPDATE t_quote SET in_ops_pool = false WHERE in_ops_pool IS NULL;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 't_quote' AND column_name = 'in_cold_start_pool'
  ) THEN
    UPDATE t_quote
    SET in_ops_pool = COALESCE(in_ops_pool, in_cold_start_pool, false);
    -- 冗余列可留着不影响；若要删掉取消下一行注释：
    -- ALTER TABLE t_quote DROP COLUMN in_cold_start_pool;
  END IF;
END $$;
