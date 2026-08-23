'''Explainable lead scoring.

Deterministic, configurable weights produce the score and a human-readable
reason list. An LLM may add a bounded contextual adjustment (default +/-8) that
is recorded separately, so the number is always traceable to rules.
'''
from __future__ import annotations
import math
from ..util import clamp, days_since

WEIGHTS_VERSION = 'v1'
BASE_SCORE = 50
AI_ADJUSTMENT_CAP = 8

DEFAULT_WEIGHTS = {
    'no_website': 20,
    'website_unreachable': 18,
    'poor_mobile_experience': 15,
    'outdated_website': 12,
    'modern_website': -20,
    'active_social': 10,
    'social_only_presence': 8,
    'multiple_branches': 8,
    'new_branch': 10,
    'expansion': 8,
    'hiring': 5,
    'advertising': 7,
    'new_product': 6,
    'rebranding': 4,
    'manual_processing': 6,
    'no_online_catalog': 5,
    'no_online_ordering': 5,
    'no_contact_form': 3,
    'no_https': 4,
    'slow_website': 4,
    'public_whatsapp': 5,
    'size_fit': 4,
    'priority_category': 8,
    'recently_verified': 5,
    'no_public_contact': -10,
    'stale_data': -5,
    'export_activity': 3,
    'insufficient_data': -15,
}

REASON_LABELS = {
    'no_website': 'Сайт не найден среди проверенных источников',
    'website_unreachable': 'Сайт указан, но не ответил при проверке',
    'poor_mobile_experience': 'Слабая мобильная версия',
    'outdated_website': 'Устаревший сайт (низкий website score)',
    'modern_website': 'Сайт современный: потребность меньше',
    'active_social': 'Активное присутствие в соцсетях',
    'social_only_presence': 'Продажи идут только через соцсети',
    'multiple_branches': 'Несколько филиалов',
    'new_branch': 'Открытие нового филиала',
    'expansion': 'Признаки роста и расширения',
    'hiring': 'Набор персонала',
    'advertising': 'Активная рекламная активность',
    'new_product': 'Запуск нового направления',
    'rebranding': 'Обновление бренда',
    'manual_processing': 'Заявки обрабатываются вручную',
    'no_online_catalog': 'Нет онлайн-каталога',
    'no_online_ordering': 'Нет онлайн-заказа или записи',
    'no_contact_form': 'Нет формы заявки',
    'no_https': 'Сайт без HTTPS',
    'slow_website': 'Медленный сайт',
    'public_whatsapp': 'Публичный WhatsApp для связи',
    'size_fit': 'Размер бизнеса подходит под предложение',
    'priority_category': 'Категория в приоритете запроса',
    'recently_verified': 'Данные проверены недавно',
    'no_public_contact': 'Не найдено контактов в проверенных источниках',
    'stale_data': 'Данные давно не проверялись',
    'export_activity': 'Внешнеэкономическая активность',
    'insufficient_data': 'Слишком мало проверенных данных для выводов',
}

SIGNAL_RULES = set(REASON_LABELS)

# Overlapping evidence must not be counted five times: 'сайта нет' plus
# 'unreachable' plus 'no catalogue' describe one weakness. Each group has a cap.
GROUP_CAPS = {
    'website_weakness': (34, ['no_website', 'website_unreachable', 'poor_mobile_experience', 'outdated_website',
                              'no_online_catalog', 'no_online_ordering', 'no_contact_form', 'no_https',
                              'slow_website']),
    'growth': (22, ['new_branch', 'expansion', 'hiring', 'advertising', 'new_product', 'rebranding',
                    'export_activity']),
    'digital_presence': (18, ['active_social', 'social_only_presence', 'public_whatsapp']),
}

def _apply_group_caps(fired):
    '''Trim positive contributions inside a group to its cap, transparently.'''
    by_code = {item['code']: item for item in fired}
    for group, (cap, codes) in GROUP_CAPS.items():
        members = [by_code[c] for c in codes if c in by_code and by_code[c]['points'] > 0]
        members.sort(key=lambda item: 0 - item['points'])
        budget = cap
        for item in members:
            if budget <= 0:
                item['capped_from'] = item['points']
                item['points'] = 0
                item['label'] = item['label'] + ' (учтено в группе ' + group + ')'
            elif item['points'] > budget:
                item['capped_from'] = item['points']
                item['points'] = budget
                item['label'] = item['label'] + ' (ограничено лимитом группы)'
                budget = 0
            else:
                budget -= item['points']
    return [item for item in fired if item['points'] != 0 or item.get('capped_from')]

def _soft_cap(raw):
    '''Keep leads distinguishable above 90 instead of flattening everything to 100.'''
    if raw <= 90:
        return clamp(raw, 0, 100)
    return 90 + 10 * (1 - math.exp(-(raw - 90) / 22.0))

def merge_weights(custom):
    weights = dict(DEFAULT_WEIGHTS)
    if isinstance(custom, dict):
        for key, value in custom.items():
            if key in DEFAULT_WEIGHTS:
                try:
                    weights[key] = int(clamp(float(value), -50, 50))
                except (TypeError, ValueError):
                    continue
    return weights

def score_lead(lead, signals_list=None, website=None, priority_categories=None, weights=None,
               ai_adjustment=0, ai_rationale=None):
    '''Return {score, reasons, confidence, weights_version, ai_adjustment}.'''
    weights = merge_weights(weights)
    signal_names = set()
    signal_conf = {}
    for sig in (signals_list or []):
        name = sig.get('signal')
        if not name:
            continue
        signal_names.add(name)
        signal_conf[name] = max(signal_conf.get(name, 0.0), float(sig.get('confidence') or 0.5))

    fired = []

    def fire(rule, evidence=None, confidence=0.8):
        points = weights.get(rule, 0)
        if points == 0:
            return
        fired.append({'code': rule, 'label': REASON_LABELS.get(rule, rule), 'points': points,
                      'evidence': evidence, 'confidence': round(confidence, 2)})

    website_known = bool(lead.get('website'))
    if not website_known:
        fire('no_website', 'среди проверенных источников сайт не указан', 0.7)
    for rule in ('website_unreachable', 'poor_mobile_experience', 'outdated_website', 'modern_website',
                 'no_online_catalog', 'no_online_ordering', 'no_contact_form', 'no_https', 'slow_website',
                 'new_branch', 'expansion', 'hiring', 'advertising', 'new_product', 'rebranding',
                 'manual_processing', 'multiple_branches', 'social_only_presence', 'no_public_contact',
                 'export_activity'):
        if rule in signal_names:
            evidence = next((s.get('evidence') for s in (signals_list or []) if s.get('signal') == rule), None)
            fire(rule, evidence, signal_conf.get(rule, 0.7))

    if any(lead.get(k) for k in ('instagram', 'telegram', 'facebook', 'telegram_channel')):
        channels = [k for k in ('instagram', 'telegram', 'facebook') if lead.get(k)]
        fire('active_social', ', '.join(channels), 0.85)
    if lead.get('whatsapp'):
        fire('public_whatsapp', lead.get('whatsapp'), 0.85)
    employees = lead.get('employees_estimate') or 0
    if 4 <= employees <= 250:
        fire('size_fit', str(employees) + ' сотрудников (оценка)', 0.6)
    if priority_categories and lead.get('category') in set(priority_categories):
        fire('priority_category', lead.get('category'), 0.9)
    core_known = sum(1 for key in ('category', 'city') if lead.get(key))
    core_known += 1 if any(lead.get(k) for k in ('phone_normalized', 'email', 'whatsapp')) else 0
    core_known += 1 if any(lead.get(k) for k in ('website', 'instagram', 'telegram', 'facebook')) else 0
    if core_known < 2:
        fire('insufficient_data', 'известно полей: ' + str(core_known) + ' из 4', 0.9)

    age = days_since(lead.get('last_verified_at'))
    if age is not None and age <= 7:
        fire('recently_verified', 'verified ' + str(round(age, 1)) + ' дн. назад', 0.9)
    elif age is not None and age > 90:
        fire('stale_data', 'last verified ' + str(round(age)) + ' дн. назад', 0.9)

    fired = _apply_group_caps(fired)
    raw_total = BASE_SCORE + sum(item['points'] for item in fired)
    adjustment = int(clamp(float(ai_adjustment or 0), -AI_ADJUSTMENT_CAP, AI_ADJUSTMENT_CAP))
    if adjustment:
        fired.append({'code': 'ai_context', 'label': 'Контекстная корректировка AI',
                      'points': adjustment, 'evidence': (ai_rationale or '')[:240], 'confidence': 0.5})
    score = int(round(_soft_cap(raw_total + adjustment)))

    completeness = 0.0
    checks = (
        (bool(lead.get('company_name')), 0.1),
        (bool(lead.get('category')), 0.1),
        (bool(lead.get('city')), 0.1),
        (bool(lead.get('phone_normalized') or lead.get('email') or lead.get('whatsapp')), 0.2),
        (website is not None, 0.2),
        (bool(signal_names), 0.15),
        (bool(lead.get('last_verified_at')), 0.15),
    )
    for ok, weight in checks:
        if ok:
            completeness += weight
    evidence_conf = sum(item['confidence'] for item in fired) / len(fired) if fired else 0.4
    confidence = round(clamp(0.5 * completeness + 0.5 * evidence_conf, 0.0, 0.99), 2)

    fired.sort(key=lambda item: 0 - abs(item['points']))
    return {'score': score, 'reasons': fired, 'confidence': confidence,
            'weights_version': WEIGHTS_VERSION, 'ai_adjustment': adjustment,
            'raw_total': raw_total}

def data_quality(lead, website=None, sources_count=1):
    '''Data-quality metadata: never presented as certainty about the business.'''
    contact_conf = 0.0
    if lead.get('phone_normalized'):
        contact_conf = max(contact_conf, 0.9)
    if lead.get('whatsapp'):
        contact_conf = max(contact_conf, 0.85)
    if lead.get('email'):
        contact_conf = max(contact_conf, 0.8)
    if lead.get('instagram') or lead.get('telegram'):
        contact_conf = max(contact_conf, 0.6)
    website_conf = 0.0
    if website is not None:
        website_conf = 0.95 if website.get('reachable') else 0.7
    elif lead.get('website'):
        website_conf = 0.4
    fields = ('company_name', 'category', 'city', 'address', 'phone_normalized', 'website',
              'description', 'employees_estimate')
    filled = sum(1 for f in fields if lead.get(f))
    quality = 0.6 * (filled / len(fields)) + 0.2 * min(1.0, sources_count / 2.0) + 0.2 * max(contact_conf, website_conf)
    return {'data_quality_score': round(clamp(quality, 0, 1), 3),
            'contact_confidence': round(contact_conf, 2),
            'website_confidence': round(website_conf, 2)}
