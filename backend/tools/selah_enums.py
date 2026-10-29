#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
Selah 内容配置枚举 — 运营台 / App / seed / 校验的唯一来源。

模块地图（均为 (value, EN label) 或辅助常量）：
- FAITH_TYPES / FAITH_ALIASES：信仰类型（exploring → learning）
- PRAYER_FREQ / INTEREST_TAGS：祷告频率与兴趣主题
- GENDERS / AGE_RANGES：基础信息
- APP_LANGS / REPORT_REASONS：App 语言与举报原因
- SAFETY_CATEGORIES / TOPIC_CATEGORIES：AI 安全类别与主题分类
- ONBOARDING_KEYS：引导题 key（basic_info 替代废弃 display_basics）

辅助：values_of / as_options / choice_tuples / normalize_faith /
validate_membership / onboarding_presets / default_onboarding_questions /
content_enums_payload / safety_*_config_key

约定：value 存库与 API；label 默认英文（安全类为中文运营说明）。
改枚举须同步 seed、前端选项与历史数据兼容（见 FAITH_ALIASES）。
"""

# (value, default EN label)

# ---------------------------------------------------------------------------
# 信仰类型：Onboarding faith_type、语录 faith_tags 校验共用
# learning = 探索中；历史客户端可能仍传 exploring
# ---------------------------------------------------------------------------
FAITH_TYPES = [
    ('new', 'New Testament'),
    ('old', 'Old Testament'),
    ('other', 'Other'),
    ('learning', 'Learning / Exploring'),
]

# 历史兼容：入库/校验时 exploring 归一为 learning
FAITH_ALIASES = {'exploring': 'learning'}

# ---------------------------------------------------------------------------
# 祷告频率：Onboarding prayer_frequency；用于画像展示，不直接驱动推荐硬过滤
# ---------------------------------------------------------------------------
PRAYER_FREQ = [
    ('daily', 'Daily'),
    ('weekly', 'Weekly'),
    ('occasionally', 'Occasionally'),
    ('just_started', 'Just starting'),
]

# ---------------------------------------------------------------------------
# 兴趣主题：Onboarding 多选 + 语录 interest_tags + 推荐 _tag_match
# value 须与运营打标、推荐 contains 查询一致
# ---------------------------------------------------------------------------
INTEREST_TAGS = [
    ('prayer', 'Prayer'),
    ('family', 'Family'),
    ('anxiety', 'Anxiety Relief'),
    ('gratitude', 'Gratitude'),
    ('courage', 'Courage'),
    ('work', 'Work & Purpose'),
    ('peace', 'Peace'),
    ('hope', 'Hope'),
    ('relationships', 'Relationships'),
    ('healing', 'Healing'),
]

# ---------------------------------------------------------------------------
# 性别 / 年龄：Onboarding basic_info 子字段；prefer_not / u18 等需注意合规展示
# ---------------------------------------------------------------------------
GENDERS = [
    ('prefer_not', 'Prefer not to say'),
    ('female', 'Female'),
    ('male', 'Male'),
    ('non_binary', 'Non-binary'),
]

AGE_RANGES = [
    ('u18', 'Under 18'),
    ('18-24', '18-24'),
    ('25-34', '25-34'),
    ('35-44', '35-44'),
    ('45-54', '45-54'),
    ('55+', '55+'),
]

# ---------------------------------------------------------------------------
# App 界面语言：与 User.system_lang、AppConfig.language、OPS_LANGUAGES 对齐
# ---------------------------------------------------------------------------
APP_LANGS = [
    ('en', 'English'),
    ('zh', '中文'),
    ('es', 'Español'),
    ('pt', 'Português'),
    ('ja', '日本語'),
    ('ko', '한국어'),
    ('ar', 'العربية'),
    ('ru', 'Русский'),
    ('fr', 'Français'),
    ('de', 'Deutsch'),
    ('fil', 'Filipino'),
    ('it', 'Italiano'),
]

# ---------------------------------------------------------------------------
# 举报原因：内容举报表单选项；other 可配自由文本
# ---------------------------------------------------------------------------
REPORT_REASONS = [
    ('inappropriate', 'Inappropriate content'),
    ('hate', 'Hate / discrimination'),
    ('spam', 'Spam / ads'),
    ('copyright', 'Copyright'),
    ('other', 'Other'),
]

# ---------------------------------------------------------------------------
# AI 安全类别：开关 key=safety_cat_{value}_enabled，拒答文案=ai_refuse_{value}
# 与 selah_quota.DEFAULTS 中同名配置对应
# ---------------------------------------------------------------------------
SAFETY_CATEGORIES = [
    ('self_harm', '自残 / 自杀'),
    ('violence', '暴力 / 伤害他人'),
    ('illegal', '违法犯罪'),
    ('hate_extremism', '仇恨 / 极端'),
    ('religion_dispute', '宗教高敏争议'),
    ('medical', '医疗处方级'),
]

# ---------------------------------------------------------------------------
# 主题分类：运营内容维度（与兴趣标签正交，偏栏目/专题）
# value 存库；label 运营台英文。App 展示用 TOPIC_CATEGORY_I18N 按 lang 返回。
# ---------------------------------------------------------------------------
TOPIC_CATEGORIES = [
    ('calm', 'Calm'),
    ('faith', 'Faith'),
    ('emotion', 'Emotion'),
    ('growth', 'Growth'),
    ('life', 'Life'),
    ('seasonal', 'Seasonal'),
    ('draw_near', 'Draw Near'),
    ('health', 'Health'),
    ('journey', 'Journey'),
]

# 主题分类多语言 label（App enums 端点按用户 lang 选取）
TOPIC_CATEGORY_I18N = {
    'calm':     {'en': 'Calm',         'zh': '平静',   'es': 'Calma',       'pt': 'Calma',       'ja': '平穏',       'ko': '평온'},
    'faith':    {'en': 'Faith',         'zh': '信心',   'es': 'Fe',          'pt': 'Fé',          'ja': '信仰',       'ko': '믿음'},
    'emotion':  {'en': 'Emotion',       'zh': '情感',   'es': 'Emoción',     'pt': 'Emoção',      'ja': '感情',       'ko': '감정'},
    'growth':   {'en': 'Growth',        'zh': '成长',   'es': 'Crecimiento', 'pt': 'Crescimento', 'ja': '成長',       'ko': '성장'},
    'life':     {'en': 'Life',          'zh': '生活',   'es': 'Vida',        'pt': 'Vida',        'ja': '人生',       'ko': '삶'},
    'seasonal': {'en': 'Seasonal',      'zh': '季节',   'es': 'Estacional',  'pt': 'Sazonal',     'ja': '季節',       'ko': '계절'},
    'draw_near':{'en': 'Draw near to God', 'zh': '亲近神', 'es': 'Acercarse a Dios', 'pt': 'Aproximar-se de Deus', 'ja': '神に近づく', 'ko': '하나님께 가까이'},
    'health':   {'en': 'Health and well-being', 'zh': '健康与身心', 'es': 'Salud y bienestar', 'pt': 'Saúde e bem-estar', 'ja': '健康とウェルビーイング', 'ko': '건강과 웰빙'},
    'journey':  {'en': 'Light for your journey', 'zh': '旅途之光', 'es': 'Luz para tu camino', 'pt': 'Luz para a jornada', 'ja': '旅路の光', 'ko': '여정을 비추는 빛'},
}

# Explore 栏目主题：运营台分类列 / 类型 slug（与 TOPIC_CATEGORIES 历史值并存）
EXPLORE_TOPIC_CATEGORIES = [
    ('draw_near', '亲近神'),
    ('health', '健康与身心'),
    ('journey', '旅途之光'),
]
TOPIC_TYPE_SLUGS = [
    ('bible_verses', '圣经经文'),
    ('prayers', '祷告'),
    ('quotes', '语录'),
    ('affirmations', '肯定语'),
]

# 主题类型 slug 多语言 label（与 TOPIC_TYPE_SLUGS value 一一对应）
TOPIC_TYPE_SLUG_I18N = {
    'bible_verses': {'en': 'Bible Verses',   'zh': '圣经经文', 'es': 'Versículos Bíblicos', 'pt': 'Versículos Bíblicos', 'ja': '聖書の御言',   'ko': '성경 말씀'},
    'prayers':      {'en': 'Prayers',         'zh': '祷告',     'es': 'Oraciones',          'pt': 'Orações',             'ja': '祈り',         'ko': '기도'},
    'quotes':       {'en': 'Quotes',          'zh': '语录',     'es': 'Citas',              'pt': 'Citações',            'ja': '名言',         'ko': '말씀'},
    'affirmations': {'en': 'Affirmations',    'zh': '肯定语',   'es': 'Afirmaciones',       'pt': 'Afirmações',          'ja': 'アファメーション', 'ko': '확언'},
}

# 默认收藏夹名字多语言（覆盖全部 APP_LANGS，与 TOPIC_*_I18N 风格对齐）
DEFAULT_FOLDER_NAME_I18N = {
    'en':  'Favorites',
    'zh':  '收藏',
    'es':  'Favoritos',
    'pt':  'Favoritos',
    'ja':  'お気に入り',
    'ko':  '즐겨찾기',
    'ar':  'المفضلة',
    'ru':  'Избранное',
    'fr':  'Favoris',
    'de':  'Favoriten',
    'fil': 'Mga Paborito',
    'it':  'Preferiti',
}

# 所有已知默认夹名的集合（用于显示层判断是否该做语言覆盖）
_DEFAULT_FOLDER_NAMES_SET = frozenset(DEFAULT_FOLDER_NAME_I18N.values())


def default_folder_name(lang):
    """根据语言码返回默认收藏夹名字；无效/缺失则 fallback 英文。"""
    if not lang:
        return DEFAULT_FOLDER_NAME_I18N['en']
    code = str(lang).strip().lower().split('-')[0]
    return DEFAULT_FOLDER_NAME_I18N.get(code, DEFAULT_FOLDER_NAME_I18N['en'])


def is_default_folder_name(name):
    """判断一个字符串是否等于任何语言的默认夹名（用于区分用户是否重命名过默认夹）。"""
    return name in _DEFAULT_FOLDER_NAMES_SET

# ---------------------------------------------------------------------------
# Onboarding 题 key：运营台题目配置与 App 答题提交共用
# basic_info 合并姓名/性别/年龄；旧 key display_basics 仅兼容读取
# label 面向运营台中文对照（value 仍为机器名，勿改）
# ---------------------------------------------------------------------------
ONBOARDING_KEYS = [
    ('faith_type', '信仰类型'),
    ('prayer_frequency', '祷告频率'),
    ('interest_tags', '兴趣主题'),
    ('basic_info', '基础信息（姓名/性别/年龄）'),
]

# Selah 2.0 扩展题（种子默认 enabled=false；运营开启后 App 可见）
ONBOARDING_KEYS_2_0 = [
    ('acquisition_channel', '获知渠道'),
    ('emotion_status', '情感/关系状态'),
    ('faith_transition', '信仰历程'),
    ('scripture_familiarity', '经文熟悉度'),
    ('faith_practice', '信仰实践'),
    ('closeness_to_god', '与神亲近度'),
    ('denomination_pref', '教派偏好'),
    ('habit_help_style', '习惯帮助方式'),
    ('relationship_with_god', '与神的关系'),
    ('recent_feeling', '最近感受'),
    ('current_struggle', '当前挣扎'),
    ('current_goal', '当前目标'),
]

INTERSTITIAL_KINDS = [
    ('personalize_intro', '个性化介绍'),
    ('prefs_intro', '偏好设置介绍'),
    ('value_edu', '价值教育'),
    ('devotion_custom', '灵修定制'),
    ('push_nudge', '推送引导'),
    ('trial_intro', '试用介绍'),
]

ONBOARDING_KEY_BASIC_INFO = 'basic_info'
ONBOARDING_KEY_BASIC_INFO_LEGACY = 'display_basics'  # 废弃 key，迁移/读旧数据时识别

# 1.0 列字段名（complete 双写用）
ONBOARDING_COLUMN_KEYS = frozenset({
    'faith_type', 'prayer_frequency', 'interest_tags',
    'display_name', 'gender', 'age_range', 'age_group',
})


def all_onboarding_key_values():
    """1.0 ∪ 2.0 question_key 白名单。"""
    return values_of(ONBOARDING_KEYS) + values_of(ONBOARDING_KEYS_2_0)


# 2.0 多选题：空答案用 []，其余单选/文本用 ''
ONBOARDING_MULTI_KEYS_2_0 = frozenset({'current_struggle', 'current_goal'})


def empty_profile_answer(key: str):
    """跳过/未答时 profile_answers 的占位空值。"""
    return [] if key in ONBOARDING_MULTI_KEYS_2_0 else ''


def ensure_profile_answers_2_0(profile=None):
    """保证 2.0 扩展题 key 都在 profile_answers 中；已有值不覆盖。"""
    out = dict(profile or {})
    for key, _ in ONBOARDING_KEYS_2_0:
        if key not in out:
            out[key] = empty_profile_answer(key)
    return out


def interstitial_kind_values():
    return values_of(INTERSTITIAL_KINDS)


def values_of(pairs):
    """从 (value, label) 列表取出 value 列表。"""
    return [v for v, _ in pairs]


def as_options(pairs):
    """转为前端选项 [{value, label}, ...]。"""
    return [{'value': v, 'label': l} for v, l in pairs]


def choice_tuples(pairs):
    """DRF ChoiceField / Django choices 格式。"""
    return [(v, l) for v, l in pairs]


def normalize_faith(value):
    """信仰值规范化：应用 FAITH_ALIASES（如 exploring→learning）。空输入回 ''。"""
    if not value:
        return value or ''
    v = str(value).strip()
    return FAITH_ALIASES.get(v, v)


def validate_membership(values, allowed_pairs, *, field_name='value'):
    """
    校验列表中的值是否属于 allowed_pairs。

    返回 (ok_list, bad_list)：
    - ok 去重保序；空串跳过
    - field_name 为 faith_tags / faith_type 时先 normalize_faith
    - bad 保留原始 strip 后的非法值，便于前端提示
    """
    allowed = set(values_of(allowed_pairs))
    ok, bad = [], []
    for raw in values or []:
        v = str(raw).strip()
        if not v:
            continue
        if field_name == 'faith_tags' or field_name == 'faith_type':
            v = normalize_faith(v)
        if v in allowed:
            if v not in ok:
                ok.append(v)
        else:
            bad.append(str(raw).strip())
    return ok, bad


def onboarding_presets():
    """运营台「填入推荐枚举」用；basic_info 选项在题目 meta 里给 genders/age_ranges。"""
    multi = lambda opts: [{**o, 'type': 'multi'} for o in opts]  # noqa: E731
    return {
        'faith_type': as_options(FAITH_TYPES),
        'prayer_frequency': as_options(PRAYER_FREQ),
        'interest_tags': as_options(INTEREST_TAGS),
        'basic_info': [],
        'acquisition_channel': as_options([
            ('tiktok', 'TikTok'), ('instagram', 'Instagram'), ('friend', 'Friend'),
            ('search', 'Search'), ('store', 'App Store'), ('other', 'Other'),
        ]),
        'emotion_status': as_options([
            ('single', 'Single'), ('dating', 'Dating'), ('married', 'Married'),
            ('prefer_not', 'Prefer not to say'),
        ]),
        'faith_transition': as_options([
            ('growing', 'Growing'), ('returning', 'Returning'), ('exploring', 'Exploring'),
            ('steady', 'Steady'),
        ]),
        'scripture_familiarity': as_options([
            ('new', 'New to scripture'), ('some', 'Some familiarity'), ('deep', 'Deep familiarity'),
        ]),
        'faith_practice': as_options([
            ('prayer', 'Prayer'), ('study', 'Study'), ('community', 'Community'), ('other', 'Other'),
        ]),
        'closeness_to_god': as_options([
            ('close', 'Close'), ('seeking', 'Seeking'), ('distant', 'Distant'),
        ]),
        'denomination_pref': as_options([
            ('none', 'No preference'), ('protestant', 'Protestant'), ('catholic', 'Catholic'),
            ('orthodox', 'Orthodox'), ('other', 'Other'),
        ]),
        'habit_help_style': as_options([
            ('reminders', 'Reminders'), ('streaks', 'Streaks'), ('community', 'Community'),
            ('quiet', 'Quiet reflection'),
        ]),
        'relationship_with_god': as_options([
            ('friend', 'Friend'), ('father', 'Father'), ('guide', 'Guide'), ('mystery', 'Mystery'),
        ]),
        'recent_feeling': as_options([
            ('peaceful', 'Peaceful'), ('anxious', 'Anxious'), ('grateful', 'Grateful'),
            ('tired', 'Tired'), ('hopeful', 'Hopeful'),
        ]),
        'current_struggle': multi(as_options([
            ('anxiety', 'Anxiety'), ('loneliness', 'Loneliness'), ('work', 'Work'),
            ('family', 'Family'), ('faith', 'Faith doubts'),
        ])),
        'current_goal': multi(as_options([
            ('peace', 'More peace'), ('prayer', 'Deeper prayer'), ('habits', 'Daily habits'),
            ('healing', 'Healing'), ('purpose', 'Purpose'),
        ])),
    }


def default_onboarding_questions():
    """
    空库 / fallback 题目结构。

    当 OnboardingQuestion 表无数据时，App/运营可回落此默认四题。
    basic_info.options 为空，性别年龄放在 meta。
    """
    return [
        {
            'question_key': 'faith_type',
            'title': 'What best describes your faith?',
            'options': as_options(FAITH_TYPES),
        },
        {
            'question_key': 'prayer_frequency',
            'title': 'How often do you pray?',
            'options': as_options(PRAYER_FREQ),
        },
        {
            'question_key': 'interest_tags',
            'title': 'What themes speak to you?',
            'options': as_options(INTEREST_TAGS),
        },
        {
            'question_key': ONBOARDING_KEY_BASIC_INFO,
            'title': 'A little about you',
            'options': [],
            'meta': {
                'genders': as_options(GENDERS),
                'age_ranges': as_options(AGE_RANGES),
            },
        },
    ]


def default_onboarding_questions_2_0():
    """2.0 扩展题结构（seed 用；默认 enabled=false；App 标题保持英文）。"""
    presets = onboarding_presets()
    # seed 标题给人看的英文问句；enums label 另用中文给运营台
    seed_titles = {
        'acquisition_channel': 'How did you hear about us?',
        'emotion_status': 'What is your relationship status?',
        'faith_transition': 'Where are you in your faith journey?',
        'scripture_familiarity': 'How familiar are you with Scripture?',
        'faith_practice': 'How do you usually practice your faith?',
        'closeness_to_god': 'How close do you feel to God right now?',
        'denomination_pref': 'Do you have a denomination preference?',
        'habit_help_style': 'How can habits best help you?',
        'relationship_with_god': 'How would you describe your relationship with God?',
        'recent_feeling': 'How have you been feeling lately?',
        'current_struggle': 'What are you struggling with right now?',
        'current_goal': 'What is your goal right now?',
    }
    multi_keys = {'current_struggle', 'current_goal'}
    out = []
    for i, (key, _) in enumerate(ONBOARDING_KEYS_2_0):
        opts = presets.get(key) or []
        clean = [{'value': o['value'], 'label': o['label']} for o in opts]
        if key in multi_keys:
            clean = [{**o, 'type': 'multi'} for o in clean]
        out.append({
            'question_key': key,
            'title': seed_titles.get(key, key),
            'options': clean,
            'sort_order': 100 + (i + 1) * 10,
        })
    return out


def safety_cat_config_key(cat_value):
    """AI 安全类别开关在 AppConfig 中的 key：safety_cat_{cat}_enabled。"""
    return f'safety_cat_{cat_value}_enabled'


def safety_refuse_config_key(cat_value):
    """AI 拒答文案在 AppConfig 中的 key：ai_refuse_{cat}。"""
    return f'ai_refuse_{cat_value}'


def content_enums_payload(lang=None):
    """
    运营台 / App 共用枚举载荷。

    safety_categories 额外带 config_key / refuse_key，便于运营台直接改 AppConfig。
    lang 不为空时 topic_categories label 按 TOPIC_CATEGORY_I18N 本地化，否则回退默认英文。
    """
    if lang:
        topic_cats = []
        all_values = []
        for v, _ in EXPLORE_TOPIC_CATEGORIES + TOPIC_CATEGORIES:
            if v not in all_values:
                all_values.append(v)
        for v in all_values:
            i18n = TOPIC_CATEGORY_I18N.get(v)
            label = (i18n.get(lang) or i18n.get('en') or v) if i18n else v
            topic_cats.append({'value': v, 'label': label})
        topic_slugs = []
        for v, _ in TOPIC_TYPE_SLUGS:
            i18n = TOPIC_TYPE_SLUG_I18N.get(v)
            label = (i18n.get(lang) or i18n.get('en') or v) if i18n else v
            topic_slugs.append({'value': v, 'label': label})
    else:
        topic_cats = as_options(EXPLORE_TOPIC_CATEGORIES + TOPIC_CATEGORIES)
        topic_slugs = as_options(TOPIC_TYPE_SLUGS)
    return {
        'onboarding_keys': as_options(ONBOARDING_KEYS),
        'onboarding_keys_2_0': as_options(ONBOARDING_KEYS_2_0),
        'interstitial_kinds': as_options(INTERSTITIAL_KINDS),
        'onboarding_presets': onboarding_presets(),
        'faith_types': as_options(FAITH_TYPES),
        'prayer_frequency': as_options(PRAYER_FREQ),
        'interest_tags': as_options(INTEREST_TAGS),
        'genders': as_options(GENDERS),
        'age_ranges': as_options(AGE_RANGES),
        'app_langs': as_options(APP_LANGS),
        'report_reasons': as_options(REPORT_REASONS),
        'safety_categories': [
            {
                'value': v,
                'label': l,
                'config_key': safety_cat_config_key(v),
                'refuse_key': safety_refuse_config_key(v),
            }
            for v, l in SAFETY_CATEGORIES
        ],
        'topic_categories': topic_cats,
        'topic_type_slugs': topic_slugs,
    }
