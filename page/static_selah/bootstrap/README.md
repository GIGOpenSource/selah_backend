# Selah 包内公开配置快照（bootstrap）— 仅本工程

发版前从运营库按 **Selah scope** 导出，写入本目录，供首次冷启秒开 Welcome / 开关 / 问卷文案。

**禁止**写入 `frontend/mobile-manna/`（那是 Manna）。Manna 请用 `export_manna_bootstrap.py`。

## 导出

在 `fast_base/backend`：

```bash
./uivsbe_env/bin/python scripts/export_selah_bootstrap.py
# 等价：
# ./uivsbe_env/bin/python scripts/export_bootstrap_snapshot.py --preset selah
# 可选：--lang zh --platform ios
```

或在本工程根目录：

```bash
npm run export:bootstrap
```

## 文件

| 文件 | 用途 |
|---|---|
| `app-config.json` | 品牌/开关/额度默认/VIP 文案等（含 `revision`） |
| `welcome.json` | Welcome 屏文案与 `version` |
| `onboarding-questions.json` | `{ revision, questions }` |
| `interstitials.json` | `{ kind: payload }` |
| `manifest.json` | 导出版本索引（含 `app: selah` + scope） |

## 运行时（`utils/bootstrap.js`）

1. `App.onLaunch` → `hydrateBootstrapCaches()`：无 pageCache 时写入快照  
2. 同时 `refreshBootstrapCaches()` 后台拉网；`revision`/`version` 一致则跳过写缓存  
3. **不随包**：Feed、用户画像、VIP 权益状态、额度账本（仍必须联网）

## 维护

- Admin 改 Welcome / AppConfig 后，**发版前重导本目录**  
- 本目录 JSON 与 Manna `mobile-manna/static/bootstrap` 无共享、勿互相覆盖  
