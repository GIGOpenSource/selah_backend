# Selah API 目录（1.0）

统一响应：`{ code, message, results }`。鉴权头：`token`。分页：`currentPage` / `pageSize`。

文档：`/api/docs/`（Swagger）  
健康检查：`GET /health` 或 `GET /api/health/`（DB + Redis）

## Auth `/api/auth/`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | device-login/ | 按 `(device_id, app_key)` 找或建用户；同机另一 App 新建 uid |
| POST | oauth-login/ | Google/Apple **独立账号**登录（与设备号不合并）；Google 信 oauth_id |
| POST | bind-oauth/ | **已废弃**（410）；请用 oauth-login 切换三方账号 |
| GET | me/ | 当前用户 |
| GET | quota/ | 额度账本 |
| POST | dismiss-bind-prompt/ | 关闭绑定弱提示 |
| POST | update-profile/ | 资料/设置 |
| POST | logout/ | 登出（仅失效 token，不删账号） |
| POST | delete-account/ | 申请注销（7 天冷静期；踢会话） |
| POST | cancel-delete/ | 冷静期内撤销注销 |

前端对接：`Doc/account-deletion-frontend.md`

## Content `/api/content/`

列表与按 ID 读/写均受 `app_key×platform`（主题/壁纸另含 `language`）可见性约束：匹配精确维 ∪ 空维全局行；用户画像空维回退 `X-App-Key` / `X-Platform`。

**口径**：用户可见文案统一称 **主题**（EN: Theme）；代码/API 路径仍用 `Topic` / `topics`（历史兼容）。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | feed/ | 冷启 70/30 Feed（Free 过滤 VIP 语录；三维可见；可混入本人已关注自我肯定，`kind=quote|affirmation`） |
| GET | quote/?id= | 语录详情（分享页按 id；须在三维可见） |
| POST | expose/ | 曝光（须可见） |
| POST | like/ | 喜欢/取消（含新人 0/5；喜欢须可见） |
| GET | newcomer-like/ | 新人喜欢进度 |
| POST | dislike/ | 不喜欢（须可见） |
| POST | collect/ / uncollect/ | 收藏（收藏须可见） |
| POST | report/ | 举报（须可见） |
| GET | topics/ | 主题列表（含 followed / locked；三维；`followed_only=1` 仅已关注） |
| GET | topic-quotes/ | 主题语录（Topic+语录均三维；进入成功即自动关注） |
| POST | follow-topic/ | 关注主题（关注须主题可见） |
| POST | clear-followed-topics/ | 清空全部关注主题 |
| GET | wallpapers/ | 壁纸（三维；`used_only=1` 当前+曾用） |
| POST | set-wallpaper/ | 设置壁纸（须可见+上架；写入使用历史） |
| POST | clear-wallpaper-usage/ | 清空曾用壁纸记录（保留当前 wallpaper_id） |
| GET | folders/ | 收藏夹列表（含 followed；无则建默认；`followed_only=1` 仅已关注） |
| POST | follow-folder/ | 关注/取消本人收藏夹 `{folder_id, follow}` |
| POST | folder-save/ | 新建/重命名收藏夹 `{id?, name}` |
| POST | folder-delete/ | 删除收藏夹（内容回默认夹；清 FolderFollow） |
| GET | my-assets/?kind=&folder_id= | 喜欢/收藏/历史；仅返回当前三维仍可见语录；收藏项含 `folder_id`；卡片含 `source_ref` |
| GET | affirmations/ | 自我肯定列表；`results.followed` 整体关注态 + `results.list`；含 liked/collected |
| POST | follow-all-affirmations/ | 一键关注/取消本人全部自我肯定 `{follow?}`（默认 true） |
| POST | affirmation-save/ | 新建/更新自我肯定（1–160 字）；**新建自动关注** |
| POST | affirmation-delete/ | 删除自我肯定（级联清喜欢/收藏/关注） |
| GET | affirmation-templates/ | 运营预置肯定语模板（登录；按 app×lang×platform 作用域） |
| POST | history-clear/ | 清空当前用户曝光历史 |
| GET | app-config/ | 公开配置（无需登录）：品牌/法务/社交 URL、额度、`trial_days`、`vip_benefits`、`vip_cta_text`、`ai_greeting`、`streak_miss_reset_days`、`welcome_enabled`、新人说明文案键、**2.0** `selah_2_0_features_enabled` / `trial_intro_enabled` / `widget_teaser_enabled` / `app_icon_teaser_enabled` / `dynamic_wallpaper_tab_enabled`、嵌入 `membership_products`、**`revision`**（白名单 key 最新 `updated_at`，供包内快照比对） |
| GET | welcome/ | 冷启 Welcome 屏（无需登录；`welcome_enabled=false` 或无启用行时 `data=null`） |
| GET | interstitial/?kind= | 教育夹层（无需登录；`kind` 必填，未知 kind→400；无启用行 `data=null`） |
| GET | enums/ | 运营枚举（信仰/兴趣/举报原因/主题分类/性别/年龄、**`onboarding_keys_2_0` / `interstitial_kinds`** 等；源自 `selah_enums`，**代码所有**） |
| GET | hotlines/ | 启用危机热线列表（可选 `?country=`；无需登录） |
| GET/POST | onboarding/questions\|complete\|skip | 引导；`questions` 含 `sort_order`；`meta.revision` 供包内快照比对；`complete` 支持 `answers{}`（题库 key→`profile_answers`，1.0 列双写，未知 key 忽略） |
| GET | widget-payload/ | **3.0** 今日语录 + 主题色 + 缩略图（Extension/Watch） |
| GET/POST | widget-prefs/ | **3.0** 用户小组件偏好 |
| GET/POST | alarm-prefs/ | 闹钟偏好 |
| GET/POST | prayer-lock-prefs/ | 祷告锁偏好 |
| GET | app-icons/ | **3.0** 可切换图标目录 |
| POST | select-app-icon/ | **3.0** 写入 `selected_icon_key` |
| GET | wallpapers/?media_type= | **3.0** 可筛 `static` / `video` |
| POST | affirmation-save/ | **3.0** 可选 `theme_id` / `wallpaper_id` / `meta`（Compose） |

### Quiz `/api/content/quiz/`（3.0）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | list/ | 可用测验列表 |
| POST | start/ | 开始作答 → attempt |
| POST | answer/ | 提交单题 |
| GET | result/ | 结果页数据（**自动完成本局**，无独立 `finish/`） |

**法务入口契约**：App **优先**运营配置的 `url_privacy` / `url_terms` / `url_help` / `url_download` / `url_subscription`；空或占位域名时回落本机 `/legal/<slug>/`。本机页正文可被 `legal_body_<slug>` 覆写。

## Checkin `/api/checkin/`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | status/ | 周条三态 / 连续天 / 爱心 / can_checkin_today |
| POST | do/ | 首开打卡 + 冻结 + Free 领次 |
| POST | prefs/ | 打卡记录/提醒开关（同步 `reminder_prefs`） |
| GET/POST | reminder-prefs/ | **2.0** 提醒中心三开关：`record_enabled` / `streak_remind_enabled` / `checkin_remind_enabled`；关 record 强制关后两者；与 `checkin_*` 列同步；GET 另含 `times` |
| GET/POST | reminder-times/ | 提醒钟点（点「完成」走 POST）：`key` + `hour`/`minute` 或 `time`=`HH:mm`；GET 回全部 `writing`/`practice`/`quotes`/`checkin`/`streak` |

## AI `/api/ai/`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | quick-prompts/ | 快捷开场 |
| GET | history/ | 对话历史；可选 `?date=YYYY-MM-DD`，返回 `dates` 列表 |
| POST | chat/ | 发送（成功扣次；安全拒答不扣；含 `conversation_id`） |
| POST | delete-message/ | 删除消息 |

## Membership `/api/membership/`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | plans/ | 套餐（含 `benefits`、`cta_text`、`vip_daily_quota`、`trial_days`；`price_display` 按语言从 `price_display_i18n` 解析） |
| POST | verify/ | 收据验单；`sandbox` 受 DEBUG + `iap_sandbox_allowed` + 生产 `ALLOW_IAP_SANDBOX` 门禁；iOS 可配 `APPLE_SHARED_SECRET` 走 verifyReceipt；生产无凭证默认拒绝（`ALLOW_IAP_UNVERIFIED=1` 联调例外） |
| POST | restore/ | 恢复购买 |

## Analytics `/api/analytics/`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | batch/ | 批量埋点入库；刷 `last_active_at`；约定仅 `ios`/`android` 写库（`h5` 可不入库） |

客户端：`utils/analytics.js`（Firebase + 本接口）。看板消费见 Admin `stats/*`。

## Admin Ops `/api/admin-ops/`（IsAdmin）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | login/ | 运营登录 |
| GET | configs/ | 配置列表；`meta.ai` 含 Key 是否配置 / API Base（Key 本身不返回） |
| POST | config-save/ | 保存配置 `{key,value,remark?}` |

常用配置键（运营台「AI Provider / 签到额度 / 冷启 / 安全」页可改）：

| 键 | 说明 |
| --- | --- |
| `safety_cat_<category>_enabled` | 安全类别开关（self_harm / violence / illegal / hate_extremism / religion_dispute / medical） |
| `ai_refuse_<category>` / `ai_refuse_general` | 拒答文案 |
| `ai_crisis_footer` | 自残拒答危机尾注 |
| `ai_provider` / `ai_model` / `ai_system_prompt` | AI Provider |
| `cold_start_interest_ratio` / `cold_start_ops_ratio` | 冷启 70/30 |
| `checkin_reward` / `free_quota_cap` / `streak_*` | 签到与冻结爱心 |
| `selah_2_0_features_enabled` / `trial_intro_enabled` | 2.0 问卷展示 / 进 VIP 前试用夹层（签到/额度页） |
| `widget_teaser_enabled` / `app_icon_teaser_enabled` / `dynamic_wallpaper_tab_enabled` | 兼容旧键；3.0 真能力以 `widget_enabled` / `app_icon_enabled` / `dynamic_wallpaper_enabled` 等为准 |
| `compose_enabled` / `quiz_enabled` / `url_rate` / `compose_entry_label` | **3.0** Compose/Quiz/Support 开关与文案 |

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET/POST | quizzes / quiz-save / quiz-delete / quiz-questions* | **3.0** 测验与题目 CRUD |
| GET/POST | app-icons / app-icon-save / app-icon-delete | **3.0** App 图标目录 |
| GET | quotes/?q=&app_key=&language=&platform=&in_cold_start_pool=&status=&tag_type=&tag_name=&trashed= | 语录列表（可筛冷启池 / 三维 / 发布态 published\|disabled 或 1\|0 / 标签 / 回收站；空维=不过滤） |
| POST | quote-save/ / quote-delete/ / quote-import/ | 语录 CRUD / Excel·CSV 导入（含 app_key×language×platform） |
| GET/POST | topics/?trashed= / topic-save / topic-delete / topic-restore | 主题（app_key×language×platform；空维=不过滤；trashed=1 回收站） |
| GET | wallpapers/?q=&app_key=&language=&platform=&status=&theme=&visual=&trashed= | 壁纸列表（三维 / 发布态 published\|disabled 或 1\|0 / 主题 / 视觉风格 / 回收站；空维=不过滤） |
| POST | wallpaper-save/ / wallpaper-delete/ | 壁纸 CRUD（含 app_key×language×platform） |
| GET/POST | onboarding-questions | 引导题（支持 delete_id；含 app_key×language×platform；空串=全局行；key 含 2.0） |
| GET/POST | welcome-screens | Welcome 屏 CRUD（title/subtitle/cta/image/version/scope） |
| GET/POST | interstitial-screens | **2.0** 教育夹层 CRUD（需 `PERM_OPS`；`kind`+Welcome 同构字段） |
| GET/POST | safety-keywords / hotlines / quick-prompts | AI 安全与快捷开场（关键词/热线支持 id 更新 enabled） |
| GET | content-enums/ | 与 App `enums/` 同构，供运营台只读参考 |
| GET | user-affirmations/?q=&user_id= | 用户肯定语检索（perm: `app_users`） |
| POST | user-affirmation-delete/ | 审核删除用户肯定语（`app_users`） |
| GET | user-collections/?q=&user_id=&folder_id= | 用户收藏夹或夹内条目（`app_users`） |
| POST | user-collection-item-delete/ | 删除单条收藏（`app_users`） |
| GET/POST | affirmation-templates / affirmation-template-delete | 肯定语模板 CRUD（perm: `ops`；三维 Scope） |

语录 `quote-save` / 卡片输出含 `source_ref`（出处，可选，最长 120）。

App Content 列表可见性：`feed` / `topics` / `topic-quotes` / `wallpapers` / `quote` 详情与互动写操作均走 `content_scope_q`（匹配精确维 ∪ 空维全局行；用户空维回退 `X-App-Key` / `X-Platform`）。

**Selah 2.0 用户字段**：`t_user.profile_answers`（扩展问卷 JSON）、`t_user.reminder_prefs`（提醒中心）；`me` / `user_public_dict` 回传。夹层表 `t_interstitial_screen`。

运营前端页面：语录（含出处）、**冷启池**、主题、壁纸、**品牌与法务**、**Welcome**、**教育夹层**、**AI Provider**（含 `ai_greeting`）、**签到/额度**（含 2.0 体验开关）、全部配置、Onboarding、安全词/热线、AI 开场、**肯定语模板**、**用户肯定语 / 用户收藏**、会员 SKU（含权益/`vip_cta_text`/sandbox 开关）。

配置化缺口与后台补充项见 `Doc/Selah-前端写死与配置化清单.md`；服务端待办见 `Doc/Selah-服务端待开发清单.md`。

**OAuth（BE-02）**：设备账号与 Google/Apple 账号 **互不合并**。`oauth-login` 只按 `(oauth_id, app_key)` 找/建三方用户，不写/不抢 `device_id`。Google 信客户端 `oauth_id`；Apple 生产必传 `id_token`，JWKS 验签且 `aud` ∈ `APPLE_OAUTH_CLIENT_ID(S)`。`bind-oauth` 已废弃（410）。产品：Android→Google，iOS→Apple。

法务占位页：`/legal/privacy|terms|subscription|help|download/`。模板遗留 `/api/users|admin|example/` **已全部卸载**。

## Manna `/api/manna/`（Phase A–E + 2.0 early）

`app_key` 白名单含 `manna`。货币账本独立于 Selah AI quota。`me` 在 manna 上下文回传 `manna_balance` / `manna_stats`。新人赠币经 `ensure_manna_wallet`（ledger `grant_newcomer`）。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | balance/ | 余额 + stats（触发新人赠币） |
| GET | daily-verse/ | 今日经文（`active_date` 优先）；**应传** `tz`（IANA）；回 `prayer_dot` / `prayer_done` / `prayer_kind` |
| POST | amen/ | 首页 Amen；**不写功课日**、**不清红点**；回 `newly_earned`（不发币） |
| GET | stats/ | 四维 + 余额 |
| GET | calendar/ | `year`/`month` 日标记；**应传** `tz`（缺省年月用用户本地月） |
| GET | calendar-day/ | 单日摘要；**应传** `tz`（缺省 `date` 用用户本地今天） |
| POST | activity-ping/ | 时长上报 `scene`/`seconds`；**应传** `tz`（记到用户本地日） |
| GET | badges/ | 徽章定义 + 获得态；含 `progress`/`target`/`cta_path`/`how_to`(按请求语言)/`hidden`；同系列只返回下一枚 |
| GET | prayer/ | `?kind=morning\|night`；经文+祷告文取自当日今日经文（与首页同一条）；`kind` 只影响晨/晚背景 |
| POST | prayer/amen/ | 祷告 Amen（完成早晚祷）；**应传** `tz`；回 `prayer_dot` / `prayer_done` / `manna_delta` 等 |
| GET | collections/ | 高亮/笔记/喜欢聚合 |
| GET | story-cards/ | 已解锁故事卡 |
| GET | ledger/ | 分页流水 |
| POST | favorite/toggle/ | 喜欢切换 |
| GET | share-payload/ | 分享文案；附带 `themes`/`default_key`/`theme_key`/`incoming_bg`；`scene=badge` 不回落经文、不带商店链 |
| GET | versecard-themes/ | Verse card 底栏主题列表（运营表；同图 URL 去重）；可选 `bg`/`theme_key` 解析选中态 |
| GET | home/cards/ | Home 运营卡；**应传** `tz`（按用户本地日筛 `active_date`） |
| GET | stats/card/ | 成长卡；**应传** `tz`。`manna`=余额；`manna_today`=今日收益（四行为+新人赠币）；`pray_min`/`devotion_min`/`read_min`/`quiz_num`（及 `pray_num`/`devotion_num`）=各行为终身获得 Manna |
| GET | testimonies/ | Testimony 列表；`?id=` 详情 |

表：`t_manna_wallet` / `t_manna_ledger` / `t_daily_verse` / `t_versecard_theme` / `t_activity_day` / `t_badge_*` / `t_prayer_content` / `t_manna_favorite` / `t_manna_home_card` 等。

## Bible `/api/bible/`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | translations/ | 译本 |
| GET | books/ | `?translation=` 书卷；可选 `tag` |
| GET | chapter/ | `translation`+`book`+`chapter`；含 `audio_duration_sec` |
| GET | search/ | `?q=` + `translation=` 经文检索 |
| GET/POST | highlights/ | 列表 / 保存；POST `delete_id` 删 |
| GET/POST | notes/ | 列表 / 保存 |
| GET | amen-verse/ | 按节 Amen 计数 |
| POST | amen-verse/ | 经文 Amen；回 `newly_earned` |
| POST | read-progress/ | 阅读秒数；**应传** `tz`（业务日以服务端+tz为准）；回 `newly_earned` |
| POST | translate/ | 实时调用 Google Translate API；body `texts[]`+`target`；须带 `X-App-Key`/`Accept-Language`/`X-Platform` → `translations[]`+`provider_name`+`live`+`stored=false`（译文不落库） |
| GET | tags/ | 内容 Tag（含 `featured_series_id`） |

听经文件（非 `/api`）：`GET /bible-media/<WEB|KJV>/<male|female>/<Book>/<NNN>.mp3`  
库内 `audio_url` 存相对路径；用户端 `GET /api/bible/chapter/` 的 `audio_url` 解析为 CDN 绝对地址（环境变量 `BIBLE_AUDIO_CDN_BASE`）。本地无文件时 `/bible-media/...` 亦可 302 到 CDN。  
Admin：`POST /api/admin-ops/bible-chapters/` 可改 `audio_url`，或 multipart `id`+`file` 上传到 GCS `bible-media/...` 并回写相对路径。

## Devotion `/api/devotion/`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | series/ | 返回 `{featured, list}`；可选 `tag`（传 `/api/bible/tags/` 的 **key**，非 display_name）；`mine=1` 时 `featured=null` 仅个人列表。`featured` 含 `preview_sessions`（前 2 节） |
| GET | series-detail/ | `?id=` 含 sessions + 进度 |
| GET | session/ | `?id=` 正文 |
| GET | progress/ | 用户进度 |
| POST | complete/ | 完成课时（幂等）；**应传** `tz`；回 `manna_delta` / `balance` / `seconds` / `newly_earned` |

推荐规则：Tag 上运营配置的 `featured_series_id`（且用户未学过）优先；否则同列表第一个 `not_started`；都没有则 `featured=null`。

## Manna Quiz `/api/quiz/`（≠ Selah `/api/content/quiz/`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | home/ | 当前关卡 + 余额 |
| GET | levels/ | 关卡锁态 |
| POST | start/ | → `attempt_id`；回 `balance`、`preview_pass_delta`、`preview_fail_delta`、`questions[]`、`mode`、`time_limit_sec`。`mode=rapid` 6 题/60s；`story_tf` 3 题/30s；默认 3 题。**不改钱包** |
| POST | answer/ | 单题对错；回 `correct` / `answer_key` / `bible_ref`。**不结算币** |
| POST | finish/ | **唯一**通关/失败入账（`quiz_pass` / `quiz_fail`）；**应传** `tz`（功课日记用户本地日）；回 `manna_delta` / `balance` / `passed` / `next_level` / `unlock_story` / `newly_earned`（徽章，不是额外发币） |
| GET | result/ | `?attempt_id=` |
| POST | spend/ | `kind=in_bible\|review` 扣币（幂等；不足 400 insufficient manna） |
| GET | stories/ | 故事列表（`available` / `locked` 顺序门；`unlock_cost: 0`） |
| POST | story-unlock/ | 学完标记，**不扣币**；回 `newly_earned` |
| GET | story-detail/ | `?id=` |

表：`t_manna_quiz_level` / `t_manna_quiz_question` / `t_manna_quiz_story` / `t_manna_quiz_attempt` / `t_manna_quiz_story_unlock`。

## Admin Ops · Manna 增量 `/api/admin-ops/`

| 路径 | perm | 说明 |
| --- | --- | --- |
| daily-verses | content | GET/POST（`delete_id`）；含 `prayer_title` / `prayer_body` |
| versecard-themes | content | GET/POST；同范围 `theme_key` 唯一，规范化 `image_url` 不可重复 |
| bible-translations | content | GET/POST |
| bible-import-chapter | content | POST JSON 导入章 |
| devotion-series / devotion-sessions | content | CRUD；课时字段：`title` / `scripture_ref`（出处）/ `scripture_text`（经文）/ `body`（文章） |
| manna-quiz-levels / manna-quiz-questions / manna-stories | content | CRUD；题目 `bible_ref`：`book`+`chapter` 必填，`verse_start`/`verse_end` 选填；目录见 `/api/bible/books/`、`/api/bible/chapter/`（对接说明 `Doc/quiz-bible-ref-frontend.md`）；故事 `blocks` 图文穿插 + `cover_image_index`（对接说明 `Doc/story-blocks-frontend.md`） |
| prayer-contents / badge-defs | ops | CRUD |
| manna-home-cards / manna-testimonies / manna-tags | content | CRUD（tags 含 `featured_series_id`） |
| manna-tag-featured | content | GET/POST 按 Tag 配置灵修推荐书目 |
| manna-stats-copies | ops | CRUD |
| manna-ledger | app_users | GET `?user_id=` 只读；可 `reason` 筛选；无 user_id 时按日汇总 |
| stats/manna-practice | ops | Manna 功课留存看板（仅 app_key=manna） |
| stats/manna-content-opens | ops | Manna 各内容打开 PV/UV（埋点 `content_open`；可筛 `content_type`/`limit`）；前端说明 `Doc/manna-content-opens-frontend.md` |

Seed：`python scripts/seed_manna.py [--covers] [--full]`  
写入 DB（`app_key=manna`，默认 language/platform 空=全端可见）：WelcomeScreen、`login_title`/`login_subtitle`、今日经文、**Verse card 主题（`t_versecard_theme`）**、晨晚祷、5 套灵修+课时、Quiz L1–L5+题目、圣经故事、徽章、Home 运营卡、WEB/KJV 核心章（Genesis/John 1；`--full` 全书）、可选 `--covers` 灵修封面。另含 `language=zh`×`platform=ios` 对照行供 Scope 验收。Admin「Manna 内容」可查改；客户端经 `/api/*` 拉取，不以本地 DEMO 为主路径。发版欢迎/登录包内快照（Manna 专用）：`python scripts/export_manna_bootstrap.py`（勿写入 mobile/）。

前端对接 Verse card 主题：`backend/Doc/versecard-themes-frontend.md`。  
灵修课时经文独立字段：`backend/Doc/devotion-session-scripture-frontend.md`。  
圣经故事图文穿插：`backend/Doc/story-blocks-frontend.md`。
