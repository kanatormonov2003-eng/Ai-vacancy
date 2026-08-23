'''Buying-signal detection. Deterministic, evidence-backed, never invented.

Every signal carries: name, polarity, source, evidence snippet and confidence.
Text signals come from publicly published business descriptions; technical ones
come from the website analyzer.
'''
from __future__ import annotations
import re

TEXT_SIGNALS = [
    ('new_branch', 'positive', 0.75, [r'новый филиал', r'новая точка', r'второй филиал', r'вторую точку',
                                       r'открыли', r'открываем', r'new branch', r'жаңы филиал']),
    ('expansion', 'positive', 0.7, [r'расширяем', r'расширение', r'увеличиваем', r'expansion', r'выходим на рынок']),
    ('hiring', 'positive', 0.65, [r'набор персонала', r'набираем', r'ваканс', r'ищем сотрудник', r'hiring',
                                  r'набор на ']),
    ('advertising', 'positive', 0.6, [r'активная реклама', r'рекламная кампания', r'продвижение', r'таргет']),
    ('new_product', 'positive', 0.6, [r'запуск', r'новое направление', r'новый продукт', r'новая услуга']),
    ('rebranding', 'positive', 0.55, [r'ребрендинг', r'обновили бренд', r'новый логотип']),
    ('manual_processing', 'positive', 0.7, [r'вручную', r'в тетрад', r'только по телефону', r'заявки принимаем по телефону',
                                            r'запись вручную', r'только по почте', r'только через instagram']),
    ('export_activity', 'positive', 0.5, [r'экспорт', r'поставляем в ']),
]

def _evidence(text, match):
    start = max(0, match.start() - 40)
    end = min(len(text), match.end() + 40)
    return text[start:end].strip()

def from_text(text, source, source_url=None):
    '''Signals detectable in a public description. Returns a list of dicts.'''
    out = []
    if not text:
        return out
    low = str(text).lower()
    for name, polarity, confidence, patterns in TEXT_SIGNALS:
        for pattern in patterns:
            m = re.search(pattern, low)
            if not m:
                continue
            out.append({'signal': name, 'polarity': polarity, 'source': source, 'source_url': source_url,
                        'evidence': _evidence(str(text), m)[:240], 'confidence': confidence})
            break
    return out

def from_profile(lead):
    '''Structural signals derived from normalised lead fields.'''
    out = []
    branches = lead.get('branches_estimate') or 0
    if branches >= 2:
        out.append({'signal': 'multiple_branches', 'polarity': 'positive', 'source': 'source_data',
                    'source_url': None, 'evidence': str(branches) + ' branches reported by source',
                    'confidence': 0.7})
    has_social = any(lead.get(k) for k in ('instagram', 'telegram', 'facebook', 'telegram_channel'))
    if has_social and not lead.get('website'):
        out.append({'signal': 'social_only_presence', 'polarity': 'positive', 'source': 'source_data',
                    'source_url': None,
                    'evidence': 'active social profile found, no website found in checked sources',
                    'confidence': 0.8})
    if not any(lead.get(k) for k in ('phone', 'email', 'whatsapp', 'instagram', 'telegram')):
        out.append({'signal': 'no_public_contact', 'polarity': 'negative', 'source': 'source_data',
                    'source_url': None, 'evidence': 'no contact channel found in checked sources',
                    'confidence': 0.7})
    return out

def from_website(analysis):
    '''Signals derived from the deterministic website analysis.'''
    out = []
    if analysis is None:
        return out
    url = analysis.get('final_url') or analysis.get('url')
    if analysis.get('error_code') == 'robots_disallowed':
        return out
    if not analysis.get('reachable'):
        out.append({'signal': 'website_unreachable', 'polarity': 'positive', 'source': 'website_analysis',
                    'source_url': url, 'evidence': 'check failed: ' + str(analysis.get('error_code') or analysis.get('http_status')),
                    'confidence': 0.85})
        return out
    scores = analysis.get('scores') or {}
    detected = analysis.get('detected') or {}
    total = analysis.get('total_score') or 0
    if scores.get('mobile', 100) < 50:
        out.append({'signal': 'poor_mobile_experience', 'polarity': 'positive', 'source': 'website_analysis',
                    'source_url': url, 'evidence': 'mobile score ' + str(scores.get('mobile')) + '/100',
                    'confidence': 0.9})
    if total < 45:
        out.append({'signal': 'outdated_website', 'polarity': 'positive', 'source': 'website_analysis',
                    'source_url': url, 'evidence': 'website score ' + str(total) + '/100', 'confidence': 0.85})
    if total >= 75:
        out.append({'signal': 'modern_website', 'polarity': 'negative', 'source': 'website_analysis',
                    'source_url': url, 'evidence': 'website score ' + str(total) + '/100', 'confidence': 0.85})
    if not detected.get('catalog'):
        out.append({'signal': 'no_online_catalog', 'polarity': 'positive', 'source': 'website_analysis',
                    'source_url': url, 'evidence': 'no catalogue detected on the checked pages', 'confidence': 0.7})
    if not detected.get('online_order') and not detected.get('booking'):
        out.append({'signal': 'no_online_ordering', 'polarity': 'positive', 'source': 'website_analysis',
                    'source_url': url, 'evidence': 'no ordering or booking flow detected', 'confidence': 0.7})
    if not detected.get('contact_form'):
        out.append({'signal': 'no_contact_form', 'polarity': 'positive', 'source': 'website_analysis',
                    'source_url': url, 'evidence': 'no contact form detected', 'confidence': 0.7})
    if analysis.get('https') is False:
        out.append({'signal': 'no_https', 'polarity': 'positive', 'source': 'website_analysis',
                    'source_url': url, 'evidence': 'site served over plain HTTP', 'confidence': 0.95})
    if (analysis.get('response_ms') or 0) > 2500:
        out.append({'signal': 'slow_website', 'polarity': 'positive', 'source': 'website_analysis',
                    'source_url': url, 'evidence': str(analysis.get('response_ms')) + ' ms response time',
                    'confidence': 0.8})
    return out
