'''Lead pipeline: sources, raw records, normalisation, dedupe, analysis, scoring.

Idempotent by construction: canonical identity is (org_id, dedupe_key) with a
unique index; an IntegrityError raised by a concurrent worker is resolved by
re-reading and merging instead of failing.
'''
from __future__ import annotations
import sqlite3
from .. import obs
from ..config import load
from ..db import repo, sqlite as db
from ..errors import AppError, ProviderError
from ..util import dumps, loads, new_id, now_iso
from . import dedupe, normalize as nz, scoring, signals as sig_mod, taxonomy
from .query import SearchQuery

SKIP_KEYS = ('phone_confidence_hint',)

def normalize_record(raw):
    '''RawRecord to normalised lead dict. Pure function, no DB access.'''
    phone, phone_conf = nz.normalize_phone(raw.phone)
    whatsapp = nz.normalize_phone(raw.whatsapp)[0] if raw.whatsapp else None
    city, region = nz.normalize_city(raw.city)
    website = nz.normalize_url(raw.website)
    socials = {}
    for value in (raw.instagram, raw.telegram, raw.facebook, raw.description):
        if value:
            socials.update(nz.extract_social(str(value)))

    def handle(value, key):
        if socials.get(key):
            return socials[key]
        if value and '/' not in str(value):
            return str(value).strip().lstrip('@').lower()
        return None

    lead = {
        'company_name': nz.company_display_name(raw.company_name),
        'normalized_name': nz.normalize_company_name(raw.company_name),
        'category': taxonomy.canonical_category(raw.category),
        'subcategory': raw.category or None,
        'city': city,
        'region': region,
        'country': 'KG',
        'phone': raw.phone,
        'phone_normalized': phone,
        'whatsapp': whatsapp or socials.get('whatsapp'),
        'telegram': handle(raw.telegram, 'telegram'),
        'email': nz.normalize_email(raw.email),
        'website': website,
        'website_domain': nz.normalize_domain(website),
        'instagram': handle(raw.instagram, 'instagram'),
        'facebook': handle(raw.facebook, 'facebook'),
        'description': raw.description,
        'address': raw.address,
        'employees_estimate': raw.employees_estimate,
        'branches_estimate': raw.branches_estimate,
        'is_demo': 1 if raw.is_demo else 0,
        'last_seen_at': now_iso(),
        'last_verified_at': now_iso(),
    }
    lead['dedupe_key'] = dedupe.dedupe_key(lead)
    lead['phone_confidence_hint'] = phone_conf
    return lead

def _payload(lead):
    return {k: v for k, v in lead.items() if k not in SKIP_KEYS}

def _valid(lead):
    if not lead.get('company_name'):
        return False
    return bool(lead.get('normalized_name') or lead.get('phone_normalized')
                or lead.get('website_domain'))
