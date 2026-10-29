-- 同机多 App = 多 uid：device_id 不再全局唯一，改为 (device_id, app_key)。
-- 在目标库执行一次（PostgreSQL）。Django 模型已同步 UniqueConstraint 名 t_user_device_id_app_key_uidx。

ALTER TABLE t_user DROP CONSTRAINT IF EXISTS t_user_device_id_key;
DROP INDEX IF EXISTS t_user_device_id_key;
DROP INDEX IF EXISTS t_user_device_id_idx;

CREATE UNIQUE INDEX IF NOT EXISTS t_user_device_id_app_key_uidx
  ON t_user (device_id, app_key)
  WHERE device_id IS NOT NULL AND device_id <> '';
