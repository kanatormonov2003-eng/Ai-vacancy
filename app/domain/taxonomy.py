'''Local business taxonomy: RU/KY/EN aliases mapped to canonical categories.'''
from __future__ import annotations

CATEGORIES: dict = {}

def _add(code, ru, ky, en, aliases):
    CATEGORIES[code] = {'ru': ru, 'ky': ky, 'en': en, 'aliases': aliases}

_add('restaurants', 'Рестораны и кафе', 'Ресторандар', 'Restaurants and cafes',
     ['ресторан', 'рестораны', 'ресторанам', 'кафе', 'столовая', 'пиццерия', 'чайхана',
      'restaurant', 'cafe', 'coffee', 'доставка еды', 'кофейня'])
_add('retail', 'Магазины и ритейл', 'Дүкөндөр', 'Retail',
     ['магазин', 'магазины', 'бутик', 'торговля', 'shop', 'store', 'retail'])
_add('auto_service', 'СТО и автосервис', 'Автосервис', 'Auto service',
     ['сто', 'автосервис', 'автомойка', 'шиномонтаж', 'автозапчасти'])
_add('hotels', 'Гостиницы', 'Мейманканалар', 'Hotels',
     ['гостиница', 'гостиницы', 'отель', 'хостел', 'hotel', 'hostel'])
_add('clinics', 'Клиники и медцентры', 'Клиникалар', 'Clinics',
     ['клиника', 'клиники', 'клиникам', 'стоматология', 'медцентр', 'clinic', 'dental'])
_add('education', 'Образовательные центры', 'Билим берүү', 'Education',
     ['образовательный центр', 'курсы', 'школа', 'учебный центр', 'school', 'courses',
      'education', 'language center'])
_add('manufacturing', 'Производство', 'Ондуруш', 'Manufacturing',
     ['производство', 'завод', 'цех', 'фабрика', 'швейный', 'factory'])
_add('construction', 'Строительство', 'Курулуш', 'Construction',
     ['строительство', 'строительная', 'строительные', 'строительным', 'ремонт квартир',
      'строитель', 'construction', 'девелопер'])
_add('wholesale', 'Оптовая торговля', 'Дң сатуу', 'Wholesale',
     ['опт', 'оптовик', 'оптовые', 'оптовая', 'wholesale', 'дистрибьютор'])
_add('beauty', 'Красота и услуги', 'Сулуулук', 'Beauty and services',
     ['салон красоты', 'барбершоп', 'парикмахерская', 'beauty', 'barber'])
_add('logistics', 'Логистика и доставка', 'Логистика', 'Logistics',
     ['логистика', 'грузоперевозки', 'карго', 'logistics', 'cargo'])
_add('other', 'Другое', 'Башка', 'Other', ['другое', 'other'])

_ALIAS_INDEX = []
for _code, _meta in CATEGORIES.items():
    _names = [_code, _meta['ru'].lower(), _meta['en'].lower()] + list(_meta['aliases'])
    for _alias in _names:
        _ALIAS_INDEX.append((_alias.lower(), _code))
_ALIAS_INDEX.sort(key=lambda pair: 0 - len(pair[0]))

def canonical_category(raw):
    if not raw:
        return None
    low = str(raw).strip().lower()
    if low in CATEGORIES:
        return low
    for alias, code in _ALIAS_INDEX:
        if alias and alias in low:
            return code
    return None

def label(code, locale='ru'):
    meta = CATEGORIES.get(code or '')
    if not meta:
        return code or 'unknown'
    return meta.get(locale) or meta['en']

def all_categories(locale='ru'):
    return [{'code': code, 'label': label(code, locale)} for code in CATEGORIES]

# --- alias matching -------------------------------------------------------
# Substring matching is wrong for short aliases: 'сто' matched inside
# 'ресторанам'. Aliases are matched on word boundaries and tolerate up to
# three trailing letters so Russian inflections still hit.
import re as _re

_ALIAS_RE = []
for _alias, _code in _ALIAS_INDEX:
    _esc = _re.escape(_alias).replace('\\ ', r'[\s-]+')
    _ALIAS_RE.append((_re.compile(r'(?<!\w)' + _esc + r'[а-яёүөңa-z]{0,3}(?!\w)', _re.I | _re.U), _code, _alias))

def find_categories(text):
    '''All canonical categories mentioned in a free-form text, ordered by appearance.'''
    if not text:
        return []
    low = str(text).lower()
    hits = []
    for pattern, code, _alias in _ALIAS_RE:
        m = pattern.search(low)
        if m and code not in [c for c, _p in hits]:
            hits.append((code, m.start()))
    return [code for code, _pos in sorted(hits, key=lambda item: item[1])]

def canonical_category(raw):
    if not raw:
        return None
    low = str(raw).strip().lower()
    if low in CATEGORIES:
        return low
    found = find_categories(low)
    return found[0] if found else None
