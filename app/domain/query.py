'''Search query model + deterministic natural-language parser.

Rules first: cities, categories, counts, score thresholds and channel conditions
are extracted by code. The LLM layer may only refine the result and can never
introduce fields (see app/ai/search_query.py).
'''
from __future__ import annotations
import re
from dataclasses import dataclass, field, asdict
from . import normalize as nz, taxonomy

SIZES = ('any', 'micro', 'small', 'medium', 'large')

@dataclass
class SearchQuery:
    text: str = ''
    cities: list = field(default_factory=list)
    categories: list = field(default_factory=list)
    priority_categories: list = field(default_factory=list)
    keywords: list = field(default_factory=list)
    limit: int = 50
    min_score: int = 0
    has_website: bool | None = None
    has_whatsapp: bool | None = None
    has_instagram: bool | None = None
    size: str = 'any'
    sources: list = field(default_factory=list)
    parsed_by: str = 'rules'
    notes: list = field(default_factory=list)

    def as_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        data = dict(data or {})
        allowed = set(cls.__dataclass_fields__)
        q = cls(**{k: v for k, v in data.items() if k in allowed})
        q.limit = max(1, min(int(q.limit or 50), 500))
        q.min_score = max(0, min(int(q.min_score or 0), 100))
        if q.size not in SIZES:
            q.size = 'any'
        q.cities = [c for c in (nz.normalize_city(c)[0] for c in q.cities) if c]
        q.categories = [c for c in (taxonomy.canonical_category(c) for c in q.categories) if c]
        q.priority_categories = [c for c in (taxonomy.canonical_category(c) for c in q.priority_categories) if c]
        q.keywords = [str(k)[:60] for k in q.keywords][:20]
        q.notes = [str(n)[:200] for n in q.notes][:20]
        return q

ALL_KG = ('весь кыргызстан', 'всей кыргызстан', 'по кыргызстану', 'all kyrgyzstan')
PRIORITY_MARKERS = ('приоритет', 'priority', 'прежде всего', 'особенно')
STOPWORDS = set('найди найти мне или для которым которые нужен нужно покажи только без новый сайт компании компаний приоритет score более больше потенциально find show leads'.split())

def parse(text, profile=None):
    '''Turn free-form Russian/English/Kyrgyz text into a structured query.'''
    raw = (text or '').strip()
    low = raw.lower()
    profile = profile or {}
    q = SearchQuery(text=raw[:2000])

    for alias, city in nz.CITY_ALIASES.items():
        if re.search(r'(?<![\w])' + re.escape(alias), low) and city not in q.cities:
            q.cities.append(city)
    if any(m in low for m in ALL_KG):
        q.cities = []
        q.notes.append('география: весь Кыргызстан')
    if not q.cities and profile.get('cities'):
        q.cities = list(profile['cities'])
        q.notes.append('города из профиля')

    q.categories = taxonomy.find_categories(low)
    if not q.categories and profile.get('categories'):
        q.categories = list(profile['categories'])
        q.notes.append('категории из профиля')

    for marker in PRIORITY_MARKERS:
        idx = low.find(marker)
        if idx == -1:
            continue
        window = low[idx:idx + 240]
        q.priority_categories = taxonomy.find_categories(window)
        break

    m = re.search(r'(\d{1,4})\s*(компан|лид|организац|клиент|compan|lead|business)', low)
    if not m:
        m = re.search(r'(?:найди|найти|find|show|покажи)\D{0,12}(\d{1,4})', low)
    if m:
        q.limit = max(1, min(int(m.group(1)), 500))

    ms = re.search(r'(?:score|скор|балл|рейтинг)\D{0,12}(\d{1,3})', low)
    if not ms:
        ms = re.search(r'(?:от|выше|above|over|>=|>)\s*(\d{1,3})\s*(?:/\s*100|бал|\b)', low)
    if ms and 0 <= int(ms.group(1)) <= 100:
        q.min_score = int(ms.group(1))
    elif profile.get('min_score'):
        q.min_score = int(profile['min_score'])
        q.notes.append('минимальный score из профиля')

    if re.search(r'(без сайт|нет сайт|нету сайт|нужен сайт|нужен новый сайт|no website|without website)', low):
        q.has_website = False
    elif re.search(r'(есть сайт|с сайтом|with website|has website)', low):
        q.has_website = True
    if re.search(r'(whatsapp|ватсап)', low):
        q.has_whatsapp = True
    if re.search(r'(instagram|инстаграм)', low):
        q.has_instagram = True
    for size, words in (('micro', ('микро', 'micro')), ('small', ('малый бизнес', 'малые', 'small')),
                        ('medium', ('средний бизнес', 'средние', 'medium')),
                        ('large', ('крупные', 'крупный', 'large'))):
        if any(w in low for w in words):
            q.size = size
            break

    q.keywords = [w for w in re.findall(r'[\wа-яңөү]{4,}', low) if w not in STOPWORDS][:12]
    return q
