# Manna 包内公开配置快照（bootstrap）— 仅本工程

发版前从运营库按 **`app_key=manna`** 导出，写入本目录，供首次冷启秒开 Welcome / 登录文案 / 开关默认。

**禁止**写入 `frontend/mobile/`（那是 Selah）。Selah 请用 `export_selah_bootstrap.py`。

## 导出

在 `backend`：

```bash
./uivsbe_env/bin/python scripts/export_manna_bootstrap.py
# 等价：
# ./uivsbe_env/bin/python scripts/export_bootstrap_snapshot.py --preset manna
# 可选：--lang zh --platform ios
```

或在本工程根目录：

```bash
npm run export:bootstrap
```

## 文件

| 文件 | 用途 |
|---|---|
| `app-config.json` | 品牌 / Manna 开关 / 登录标题副标题 / VIP 文案等（含 `revision`） |
| `welcome.json` | Welcome 屏文案与 `version` |
| `onboarding-questions.json` | `{ revision, questions }`（冷启：CMS 有启用题且用户未 complete/skip 才进问卷页） |
| `interstitials.json` | `{ kind: payload }`（VIP `trial_intro` 等） |
| `manifest.json` | 导出版本索引（含 `app: manna` + scope） |

## 运行时（`utils/bootstrap.js`）

1. `App.onLaunch` → `hydrateBootstrapCaches()`：无 `manna_page_cache` 时写入快照  
2. 同时 `refreshBootstrapCaches()` 后台拉网；`revision`/`version` 一致则跳过写缓存  
3. **不随包**：Bible / Quiz / Devotion / Prayer / Me 业务数据、余额账本、VIP 权益状态（仍必须联网）

## 维护

- Admin Scope = `manna`（× 语言 × 设备）改 Welcome / `login_title` 后，**发版前重导本目录**  
- 本目录 JSON 与 Selah `mobile/static/bootstrap` 无共享、勿互相覆盖  
