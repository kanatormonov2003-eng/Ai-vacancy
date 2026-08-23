'''Real HTTP provider for a paginated public business directory JSON API.

This is a production integration path, not a stub: real sockets, real JSON, the
hardened client (timeouts, retries, robots, circuit breaker, cache). It reports
itself unavailable when DIRECTORY_BASE_URL is unset so the app degrades instead
of pretending. The integration suite drives it against a real local HTTP server.
'''
from __future__ import annotations
import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from .. import obs
from ..analysis.http_client import fetch
from ..config import load
from ..errors import ProviderError
from .base import LeadSource, RawRecord, register

ITEM_KEYS = ('items', 'data', 'results', 'companies')
OWNED_PARAMS = ('page', 'city', 'category')

@register
class HttpDirectoryProvider(LeadSource):
    name = 'http_directory'
    is_demo = False
    description = 'Paginated public business directory over HTTP JSON (set DIRECTORY_BASE_URL)'

    def available(self):
        cfg = load()
        if not cfg.directory_base_url:
            return False, 'DIRECTORY_BASE_URL is not configured'
        return True, 'ok'

    def _url(self, query, page):
        """Build the request URL.

        P0-4: values used to be concatenated raw, so a Cyrillic city crashed the
        socket write with UnicodeEncodeError, and a value like Bishkek&admin=1
        injected an extra query parameter. urlencode() percent-encodes both.
        page/city/category already present in the configured base URL are dropped
        so stale config cannot override the caller.
        """
        base = (load().directory_base_url or '').strip()
        parts = urlsplit(base)
        if parts.scheme not in ('http', 'https') or not parts.netloc:
            raise ProviderError('DIRECTORY_BASE_URL must be an absolute http(s) URL')
        params = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                  if k not in OWNED_PARAMS]
        params.append(('page', str(int(page))))
        if query.cities:
            params.append(('city', ','.join(str(c) for c in query.cities)))
        if query.categories:
            params.append(('category', ','.join(str(c) for c in query.categories)))
        path = parts.path.rstrip('/')
        return urlunsplit((parts.scheme, parts.netloc, path, urlencode(params, doseq=True), ''))

    def fetch(self, query, limit=50):
        cfg = load()
        usable, reason = self.available()
        if not usable:
            raise ProviderError(self.name + ' unavailable: ' + reason)
        headers = {'Authorization': 'Bearer ' + cfg.directory_api_key} if cfg.directory_api_key else None
        emitted, page, pages = 0, 1, 1
        while emitted < limit and page <= pages and page <= 50:
            self.limiter.wait()
            url = self._url(query, page)
            res = fetch(url, provider='source:' + self.name, cache_ttl_s=900, headers=headers)
            if not res.ok:
                obs.warn('source.fetch_failed', source=self.name, code=res.error_code, status=res.status)
                retryable = res.error_code in ('timeout', 'network_error') or (res.status or 0) >= 500
                raise ProviderError(self.name + ' returned ' + str(res.error_code or res.status), retryable=retryable)
            try:
                payload = json.loads(res.body)
            except (ValueError, TypeError):
                obs.warn('source.invalid_json', source=self.name, url=url)
                raise ProviderError(self.name + ' returned malformed JSON')
            if not isinstance(payload, dict):
                raise ProviderError(self.name + ' returned an unexpected payload type')
            items = None
            for key in ITEM_KEYS:
                if isinstance(payload.get(key), list):
                    items = payload[key]
                    break
            if items is None:
                obs.warn('source.no_items', source=self.name, keys=list(payload)[:8])
                return
            try:
                pages = max(1, min(int(payload.get('pages', 1)), 50))
            except (TypeError, ValueError):
                pages = 1
            for item in items:
                if emitted >= limit:
                    return
                if not isinstance(item, dict):
                    obs.incr('source.record_skipped', source=self.name, reason='not_object')
                    continue
                name = str(item.get('name') or item.get('title') or '').strip()
                if not name:
                    obs.incr('source.record_skipped', source=self.name, reason='no_name')
                    continue
                emitted += 1
                yield RawRecord(
                    source=self.name,
                    external_id=str(item.get('id') or item.get('slug') or name)[:120],
                    company_name=name,
                    source_url=item.get('url') or url,
                    is_demo=False,
                    category=item.get('category') or item.get('rubric'),
                    city=item.get('city') or item.get('town'),
                    phone=item.get('phone') or item.get('tel'),
                    email=item.get('email'),
                    website=item.get('website') or item.get('site'),
                    instagram=item.get('instagram'),
                    telegram=item.get('telegram'),
                    facebook=item.get('facebook'),
                    whatsapp=item.get('whatsapp'),
                    address=item.get('address'),
                    description=item.get('description') or item.get('about'),
                    employees_estimate=item.get('employees'),
                    branches_estimate=item.get('branches'),
                    extra={'page': page},
                ).sanitized()
            page += 1
