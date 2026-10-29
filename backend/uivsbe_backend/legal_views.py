#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""法务 / 帮助 / 下载页。

契约（BE-05 / BE-12）:
1. App 正式入口优先读 app-config 的 url_privacy / url_terms / url_help / url_download / url_subscription。
2. url_* 为空或仍为文档占位域名时，回落到本机 /legal/<slug>/（见 content.app_config）。
3. 本页正文：优先 AppConfig.legal_body_<slug>（运营台「品牌与法务」可配）；空则用下方内置草稿。
4. 作用域：X-App-Key 或 ?app_key=；缺省时内置草稿用 {Brand} 占位，再按配置品牌替换。
5. 上架前应用正式主体名、支持邮箱，并尽量改为外链托管正文，避免长期双源。
"""
from django.http import HttpResponse
from django.utils.html import escape

from tools.selah_quota import (
    get_config,
    normalize_app_key,
    set_config_context_from_request,
)


PAGES = {
    'privacy': {
        'title': 'Privacy Policy',
        'body': (
            '{Brand} (“we”, “us”, or “our”) provides a mobile application for daily affirmations, '
            'gentle devotion, check-ins, wallpapers, and optional AI reflection features.\n\n'
            'We collect: a locally generated device identifier; optional Google/Apple bind identifiers; '
            'profile preferences you choose to provide; product analytics events; user content '
            '(likes, collections, self-affirmations, AI messages); and store transaction identifiers '
            'needed to validate subscriptions. Payment cards are processed by Apple or Google.\n\n'
            'If you save a share card, we write an image to your photo library with permission. '
            'We do not require precise location, contacts, microphone, or camera for core 1.0 features.\n\n'
            'We use data to operate the App, personalize feeds, enforce quotas and VIP, schedule local '
            'reminders you enable, improve quality, comply with law, and send marketing only if you opt in.\n\n'
            'AI replies are processed by a configurable third-party provider and are informational only — '
            'not pastoral, medical, legal, or mental-health advice.\n\n'
            'We do not sell personal information. We share with infrastructure vendors, AI providers, '
            'Apple/Google for auth and purchase validation, and authorities when legally required.\n\n'
            'Request access or deletion via {Support}. {Brand} is not directed to children under 13 '
            '(or the minimum age required in your country).\n\n'
            'This page is a counsel-ready draft; replace entity name and finalize before store release.'
        ),
    },
    'terms': {
        'title': 'Terms of Use',
        'body': (
            'By using {Brand} you agree to these Terms and our Privacy Policy.\n\n'
            'Default access uses a device-based account. Binding Google or Apple helps preserve '
            'collections after reinstall. Do not abuse the service, bypass quotas or payments, '
            'upload unlawful content, or treat AI/affirmations as professional advice.\n\n'
            'If you are in crisis, contact local emergency services or listed helplines.\n\n'
            'Paid features are auto-renewing subscriptions billed by Apple App Store / Google Play. '
            'See Subscription Terms and the store product page. Prices are set by the stores.\n\n'
            'THE APP IS PROVIDED “AS IS” TO THE MAXIMUM EXTENT PERMITTED BY LAW. '
            'Governing law to be completed by counsel for the legal entity named below.\n\n'
            'Contact: {Support}'
        ),
    },
    'subscription': {
        'title': 'Subscription Terms',
        'body': (
            '{Brand} VIP may be offered as monthly and/or yearly auto-renewing subscriptions. '
            'Exact prices and product IDs appear in the App Store or Google Play at purchase time.\n\n'
            'Where offered, a 3-day free trial is an official store introductory offer. Unless canceled '
            'under store rules before conversion, the paid plan begins and your payment method is charged.\n\n'
            'Cancel in Apple ID → Subscriptions or Google Play → Payments & subscriptions. '
            'Deleting the App does not cancel a subscription.\n\n'
            'Use Restore Purchases after reinstall. Refunds follow Apple/Google policies.'
        ),
    },
    'help': {
        'title': 'Help & FAQ',
        'body': (
            'Q: Do I need an account?\n'
            'A: {Brand} creates a lightweight account from your device ID. Bind Google/Apple to keep favorites.\n\n'
            'Q: What does Free include?\n'
            'A: Daily affirmations, limited AI quota, free wallpapers, and core sharing.\n\n'
            'Q: What does VIP unlock?\n'
            'A: Higher AI limits, VIP topics/wallpapers where configured, watermark control, and benefits on the VIP page.\n\n'
            'Q: How do check-ins work?\n'
            'A: Daily streaks with hearts to freeze short misses; Free users may earn AI quota on check-in.\n\n'
            'Q: How do I contact support?\n'
            'A: Email {Support} and include your User ID from Settings.'
        ),
    },
    'download': {
        'title': 'Download {Brand}',
        'body': (
            '{Brand} — daily affirmations and gentle devotion for iOS and Android.\n\n'
            'Official store links will replace this placeholder at launch.\n'
            'Share copy may use: Source: this page URL.'
        ),
    },
}

# Manna 专有：经文页内翻译的版权口径（仅 app_key=manna 且未运营覆写时追加）
MANNA_LEGAL_EXTRAS = {
    'privacy': (
        'Scripture translation: When you use in-app Bible translation, {Brand} sends the verse '
        'text you request to the Google Translate API and returns a live rendering for on-screen '
        'display. Translation is generated in real time when you tap Translate. We do not save, '
        'catalog, or publish the translated text as a stored Bible edition. The licensed or '
        'public-domain source text remains the canonical copy. Google and Google Translate are '
        'trademarks of Google LLC.'
    ),
    'terms': (
        'Scripture and machine translation: Bible text shown in {Brand} is from licensed or '
        'public-domain editions (for example WEB or KJV where used). Optional translation is '
        'provided solely by calling the Google Translate API in real time when you tap Translate. '
        'Machine output is for convenience only, may be inaccurate, is not an official Bible '
        'translation, and is not stored by {Brand} as a substitute edition. You may not scrape, '
        'republish, or commercially exploit displayed translations as a new Bible version.'
    ),
    'help': (
        'Q: How does Bible translation work?\n'
        'A: {Brand} calls the Google Translate API at the moment you request it. The result is '
        'shown on this page only and is not saved as a translated Bible. Use “Show original” to '
        'return to the source text.'
    ),
}


def legal_page(request, slug):
    page = PAGES.get(slug)
    if not page:
        return HttpResponse('Not found', status=404)
    # 按 X-App-Key / ?app_key= / language / platform 读运营覆写（免登录）
    set_config_context_from_request(request, prefer_user=True)
    app = normalize_app_key(
        request.headers.get('X-App-Key')
        or request.headers.get('app-key')
        or request.GET.get('app_key')
        or ''
    )
    if app:
        entity = get_config('legal_entity_name') or '[Pending legal entity name]'
        brand = get_config('brand_name') or ('Manna' if app == 'manna' else 'Selah')
        support = get_config('support_email') or (
            'support@manna.app' if app == 'manna' else 'support@selah.app'
        )
        override = (get_config(f'legal_body_{slug}') or '').strip()
    else:
        # 无 App 上下文：中性草稿，避免浏览器直开默认成 Selah 品牌
        entity = '[Pending legal entity name]'
        brand = 'the App'
        support = 'support@example.com'
        override = ''
    raw_body = override if override else page['body']
    if app == 'manna' and not override:
        extra = MANNA_LEGAL_EXTRAS.get(slug)
        if extra:
            raw_body = raw_body.rstrip() + '\n\n' + extra
    title_tpl = page['title']
    raw_body = (
        raw_body
        .replace('{Brand}', str(brand))
        .replace('{Support}', str(support))
        .replace('support@selah.app', str(support))
        .replace('Selah', str(brand))
    )
    if not app:
        raw_body += (
            '\n\n—\n'
            'Brand-specific terms: open with ?app_key=selah or ?app_key=manna '
            '(or send X-App-Key).'
        )
    title = escape(title_tpl.replace('{Brand}', str(brand)).replace('Selah', str(brand)))
    body = escape(raw_body).replace('\n', '<br>\n')
    entity_e = escape(str(entity))
    brand_e = escape(str(brand))
    support_e = escape(str(support))
    q = f'?app_key={escape(app)}' if app else ''
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title} · {brand_e}</title>
  <style>
    body {{ margin:0; font-family: "Libre Baskerville", Georgia, "Iowan Old Style", serif;
      background:#f5f5f5; color:#191919; }}
    main {{ max-width:720px; margin:0 auto; padding:48px 24px 80px; line-height:1.55; }}
    h1 {{ font-size:1.75rem; font-weight:700; letter-spacing:-0.02em; }}
    .meta {{ color:#5c5c5c; font-size:.9rem; margin:12px 0 28px; }}
    .nav a {{ color:#191919; opacity:.55; margin-right:16px; text-decoration:underline; font-size:.9rem; }}
    .card {{ background:#fff; border-radius:16px; padding:24px; }}
  </style>
</head>
<body>
  <main>
    <nav class="nav">
      <a href="/legal/privacy/{q}">Privacy</a>
      <a href="/legal/terms/{q}">Terms</a>
      <a href="/legal/subscription/{q}">Subscription</a>
      <a href="/legal/help/{q}">Help</a>
      <a href="/legal/download/{q}">Download</a>
    </nav>
    <h1>{title}</h1>
    <p class="meta">{brand_e} · {entity_e}<br/>Contact: {support_e}</p>
    <div class="card">{body}</div>
  </main>
</body>
</html>"""
    return HttpResponse(html)
