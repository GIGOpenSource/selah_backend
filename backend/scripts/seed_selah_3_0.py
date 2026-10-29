#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""Seed Selah 3.0：Quiz ≥10 题 + 默认 App icons + DEFAULTS 补齐。"""
import os
import sys

import django

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'uivsbe_backend.settings.dev')
django.setup()

from models.models import Quiz, QuizQuestion, AppIcon
from tools.selah_quota import ensure_default_configs


QUIZ_BANK = [
    {
        'title': 'Selah Bible Basics',
        'subtitle': 'Gentle questions to grow in faith',
        'questions': [
            ('Who created the heavens and the earth?', [('a', 'God'), ('b', 'Chance'), ('c', 'Nature')], 'a'),
            ('Jesus is often called the…', [('a', 'Prince of Peace'), ('b', 'King of Chaos'), ('c', 'Lord of War')], 'a'),
            ('The shortest verse in many English Bibles is…', [('a', 'Jesus wept'), ('b', 'Go'), ('c', 'Amen')], 'a'),
            ('Faith is confidence in what we…', [('a', 'hope for'), ('b', 'fear'), ('c', 'forget')], 'a'),
            ('Love is patient, love is…', [('a', 'kind'), ('b', 'proud'), ('c', 'harsh')], 'a'),
            ('The Lord is my…', [('a', 'shepherd'), ('b', 'judge only'), ('c', 'enemy')], 'a'),
            ('Blessed are the peacemakers, for they will be called…', [('a', 'children of God'), ('b', 'warriors'), ('c', 'rulers')], 'a'),
            ('Do not be anxious about…', [('a', 'tomorrow'), ('b', 'prayer'), ('c', 'hope')], 'a'),
            ('In the beginning was the…', [('a', 'Word'), ('b', 'storm'), ('c', 'silence')], 'a'),
            ('Give thanks in…', [('a', 'all circumstances'), ('b', 'only success'), ('c', 'never')], 'a'),
            ('Be still, and know that I am…', [('a', 'God'), ('b', 'busy'), ('c', 'absent')], 'a'),
            ('The fruit of the Spirit includes…', [('a', 'love'), ('b', 'envy'), ('c', 'rage')], 'a'),
        ],
    },
]

ICONS = [
    {'icon_key': 'default', 'title': 'Default', 'preview_url': '/static/ui/grid-app-icon.png', 'is_vip': False, 'sort_order': 0},
    {'icon_key': 'cross_light', 'title': 'Cross Light', 'preview_url': '/static/ui/icons/cross-light.svg', 'is_vip': False, 'sort_order': 1},
    {'icon_key': 'cross_dark', 'title': 'Cross Dark', 'preview_url': '/static/ui/icons/cross-dark.svg', 'is_vip': True, 'sort_order': 2},
]


def seed_quiz():
    for pack in QUIZ_BANK:
        quiz, created = Quiz.objects.get_or_create(
            title=pack['title'],
            language='',
            app_key='selah',
            platform='',
            defaults={
                'subtitle': pack['subtitle'],
                'enabled': True,
                'sort_order': 0,
            },
        )
        if not created and quiz.is_deleted:
            quiz.is_deleted = False
            quiz.enabled = True
            quiz.save(update_fields=['is_deleted', 'enabled'])
        existing = QuizQuestion.objects.filter(quiz_id=quiz.id).count()
        if existing >= 10:
            print(f'quiz#{quiz.id} already has {existing} questions')
            continue
        for i, (stem, options, correct) in enumerate(pack['questions']):
            QuizQuestion.objects.get_or_create(
                quiz_id=quiz.id,
                stem=stem,
                defaults={
                    'options': [{'key': k, 'text': t} for k, t in options],
                    'correct_key': correct,
                    'sort_order': i,
                    'enabled': True,
                },
            )
        print(f'seeded quiz#{quiz.id} questions={QuizQuestion.objects.filter(quiz_id=quiz.id).count()}')


def seed_icons():
    for row in ICONS:
        obj, created = AppIcon.objects.get_or_create(
            icon_key=row['icon_key'],
            app_key='selah',
            language='',
            platform='',
            defaults={
                'title': row['title'],
                'preview_url': row['preview_url'],
                'is_vip': row['is_vip'],
                'sort_order': row['sort_order'],
                'enabled': True,
            },
        )
        print(('created' if created else 'exists'), obj.icon_key)


def main():
    ensure_default_configs()
    seed_quiz()
    seed_icons()
    print('selah 3.0 seed done')


if __name__ == '__main__':
    main()
