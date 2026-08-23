'''Provider abstraction. Adding a source must never require touching the pipeline.'''
from __future__ import annotations
import threading, time
from dataclasses import dataclass, field
from .. import obs
from ..config import load
from ..util import now_iso, strip_control

@dataclass
class RawRecord:
    '''Untrusted data exactly as a source returned it. Normalised downstream.'''
    source: str
    external_id: str | None
    company_name: str
    source_url: str | None = None
    is_demo: bool = False
    category: str | None = None
    city: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    instagram: str | None = None
    telegram: str | None = None
    facebook: str | None = None
    whatsapp: str | None = None
    address: str | None = None
    description: str | None = None
    employees_estimate: int | None = None
    branches_estimate: int | None = None
    extra: dict = field(default_factory=dict)
    collected_at: str = field(default_factory=now_iso)

    def sanitized(self):
        '''Bound and clean every free-text field coming from the outside world.'''
        self.company_name = strip_control(str(self.company_name or ''), 300)
        self.description = strip_control(str(self.description or ''), 2000) or None
        self.address = strip_control(str(self.address or ''), 300) or None
        self.category = strip_control(str(self.category or ''), 120) or None
        self.city = strip_control(str(self.city or ''), 120) or None
        for attr in ('phone', 'email', 'website', 'instagram', 'telegram', 'facebook', 'whatsapp', 'source_url'):
            val = getattr(self, attr)
            if val is not None:
                setattr(self, attr, strip_control(str(val), 500) or None)
        for attr in ('employees_estimate', 'branches_estimate'):
            val = getattr(self, attr)
            if val is None:
                continue
            try:
                num = int(val)
            except (TypeError, ValueError):
                setattr(self, attr, None)
                continue
            setattr(self, attr, num if 0 < num < 1000000 else None)
        if not isinstance(self.extra, dict):
            self.extra = {}
        return self

class SourceRateLimiter:
    '''Politeness limiter: never hammer a source faster than configured.'''

    def __init__(self, per_minute):
        self.interval = 60.0 / max(1, per_minute)
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self):
        with self._lock:
            delta = self.interval - (time.monotonic() - self._last)
            if delta > 0:
                time.sleep(min(delta, 5.0))
            self._last = time.monotonic()

class LeadSource:
    name = 'base'
    is_demo = False
    description = ''

    def __init__(self):
        self.limiter = SourceRateLimiter(load().source_rate_per_min)

    def available(self):
        '''(usable, reason). Missing credentials must degrade, never crash.'''
        return True, 'ok'

    def fetch(self, query, limit=50):
        raise NotImplementedError

    def health(self):
        from ..analysis import circuit
        usable, reason = self.available()
        return {'name': self.name, 'is_demo': self.is_demo, 'available': usable, 'reason': reason,
                'circuit': circuit.state('source:' + self.name), 'description': self.description}

_REGISTRY = {}

def register(cls):
    _REGISTRY[cls.name] = cls
    return cls

def available_sources():
    return dict(_REGISTRY)

def build(names=None):
    cfg = load()
    wanted = list(names) if names else list(cfg.enabled_sources)
    out = []
    for name in wanted:
        cls = _REGISTRY.get(name)
        if cls is None:
            obs.warn('source.unknown', source=name)
            continue
        source = cls()
        usable, reason = source.available()
        if not usable:
            obs.warn('source.unavailable', source=name, reason=reason)
            continue
        out.append(source)
    return out

def load_all_providers():
    from . import demo_kg, http_directory  # noqa: F401  (import registers providers)
