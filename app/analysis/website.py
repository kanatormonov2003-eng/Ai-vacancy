"""Deterministic website analyzer. No LLM is involved in any technical verdict."""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from .. import obs
from ..domain import normalize as nz
from ..util import clamp, now_iso
from . import htmlparse
from .http_client import fetch, HttpResult

CTA_WORDS = ["заказ", "заказать", "купить", "записаться", "заявка", "связаться", "консультация",
             "order", "buy", "book", "contact", "request", "бронь", "бронирование", "буюртма"]
CATALOG_WORDS = ["каталог", "продукция", "товары", "меню", "услуги", "catalog", "catalogue", "products", "menu", "services", "shop"]
PRICE_PATTERNS = [re.compile(r"\d[\d\s.,]{2,}\s*(сом|КГС|kgs|som)", re.I), re.compile(r"(цена|прайс|price|баасы)", re.I)]
ORDER_WORDS = ["корзина", "оформить заказ", "add to cart", "checkout", "онлайн-заказ", "заказать онлайн"]
BOOKING_WORDS = ["запись онлайн", "бронирование", "booking", "reserve", "забронировать", "записаться на прием"]
NAV_WORDS = ["о нас", "контакт", "услуги", "главная", "about", "contacts", "home", "байланыш"]
LANG_HINTS = {"ru": ["рус", "/ru", "lang=ru"], "ky": ["кырг", "/ky", "/kg", "lang=ky"], "en": ["english", "/en", "lang=en"]}

@dataclass
class Fact:
    key: str
    value: str
    source: str
    source_url: str | None = None
    confidence: float = 0.99

    def as_dict(self) -> dict:
        return {"fact": self.key, "value": self.value, "source": self.source,
                "source_url": self.source_url, "confidence": self.confidence, "checked_at": now_iso()}

@dataclass
class WebsiteAnalysis:
    url: str
    reachable: bool = False
    final_url: str | None = None
    http_status: int | None = None
    https: bool = False
    ssl_valid: bool | None = None
    redirects: int = 0
    response_ms: int | None = None
    html_bytes: int = 0
    scores: dict = field(default_factory=dict)
    total_score: int = 0
    facts: list = field(default_factory=list)
    detected: dict = field(default_factory=dict)
    error_code: str | None = None

    def as_dict(self) -> dict:
        return {
            "url": self.url, "final_url": self.final_url, "reachable": self.reachable,
            "http_status": self.http_status, "https": self.https, "ssl_valid": self.ssl_valid,
            "redirects": self.redirects, "response_ms": self.response_ms, "html_bytes": self.html_bytes,
            "scores": self.scores, "total_score": self.total_score,
            "facts": [f.as_dict() if isinstance(f, Fact) else f for f in self.facts],
            "detected": self.detected, "error_code": self.error_code,
        }

def _contains_any(text: str, words: list[str]) -> str | None:
    low = text.lower()
    for w in words:
        if w in low:
            return w
    return None

def _score_technical(res: HttpResult, ex: htmlparse.Extracted, sitemap_ok: bool, robots_ok: bool) -> tuple[int, list[str]]:
    pts, notes = 0, []
    if res.https:
        pts += 22
        notes.append("HTTPS")
    else:
        notes.append("no HTTPS")
    if res.https and res.tls_ok:
        pts += 13
    elif res.https and res.tls_ok is False:
        notes.append("invalid TLS certificate")
    if res.status == 200:
        pts += 15
    elif res.status and res.status < 400:
        pts += 8
    if res.redirects <= 1:
        pts += 8
    if ex.title:
        pts += 12
    else:
        notes.append("no <title>")
    if ex.metas.get("description"):
        pts += 12
    else:
        notes.append("no meta description")
    if ex.lang:
        pts += 5
    if ex.charset or True:
        pts += 3
    if sitemap_ok:
        pts += 5
    if robots_ok:
        pts += 5
    return int(clamp(pts, 0, 100)), notes

def _score_mobile(ex: htmlparse.Extracted, html: str) -> tuple[int, list[str]]:
    pts, notes = 0, []
    viewport = ex.metas.get("viewport", "")
    if viewport and "width=device-width" in viewport.replace(" ", ""):
        pts += 45
    elif viewport:
        pts += 20
        notes.append("viewport present but not device-width")
    else:
        notes.append("no viewport meta tag")
    if re.search(r"@media[^{]*\(\s*(max|min)-width", html, re.I):
        pts += 20
    else:
        notes.append("no responsive media queries in inline CSS")
    if ex.images_responsive or ex.has_picture:
        pts += 10
    if not ex.fixed_widths:
        pts += 15
    else:
        notes.append(f"fixed pixel widths up to {max(ex.fixed_widths)}px")
    if ex.tables > 3 and ex.tag_counts.get("div", 0) < ex.tables:
        notes.append("table-based layout")
    else:
        pts += 5
    if ex.has_flash:
        pts = max(0, pts - 20)
        notes.append("Flash content")
    if re.search(r"(bootstrap|tailwind|foundation|bulma|flex|grid-template)", html, re.I):
        pts += 5
    return int(clamp(pts, 0, 100)), notes

def _score_performance(res: HttpResult, ex: htmlparse.Extracted, html_bytes: int) -> tuple[int, list[str]]:
    pts, notes = 0, []
    ms = res.elapsed_ms or 0
    if ms <= 400:
        pts += 40
    elif ms <= 900:
        pts += 32
    elif ms <= 2000:
        pts += 20
    elif ms <= 4000:
        pts += 10
        notes.append(f"slow response {ms}ms")
    else:
        notes.append(f"very slow response {ms}ms")
    if html_bytes <= 100_000:
        pts += 20
    elif html_bytes <= 400_000:
        pts += 12
    else:
        pts += 4
        notes.append(f"heavy HTML ({html_bytes // 1024} KB)")
    if ex.scripts <= 10:
        pts += 15
    elif ex.scripts <= 25:
        pts += 8
    else:
        notes.append(f"{ex.scripts} script tags")
    if ex.images <= 30:
        pts += 10
    else:
        notes.append(f"{ex.images} images on one page")
    enc = (res.headers.get("content-encoding") or "").lower()
    if any(x in enc for x in ("gzip", "br", "deflate", "zstd")):
        pts += 10
    else:
        notes.append("no HTTP compression")
    if res.headers.get("cache-control") or res.headers.get("etag"):
        pts += 5
    return int(clamp(pts, 0, 100)), notes

def _score_ux(ex: htmlparse.Extracted, text: str, contacts: dict) -> tuple[int, list[str]]:
    pts, notes = 0, []
    if ex.nav_elements or sum(1 for _h, t in ex.links if _contains_any(t, NAV_WORDS)) >= 2:
        pts += 18
    else:
        notes.append("no recognisable navigation")
    if ex.headings.get("h1"):
        pts += 15
    else:
        notes.append("no H1 heading")
    if _contains_any(text, CTA_WORDS) or ex.buttons:
        pts += 17
    else:
        notes.append("no visible call to action")
    if contacts.get("phone") or contacts.get("email"):
        pts += 20
    else:
        notes.append("no contact details found on the page")
    if ex.forms:
        pts += 15
    else:
        notes.append("no contact/request form")
    words = len(text.split())
    if words >= 250:
        pts += 15
    elif words >= 80:
        pts += 8
    else:
        notes.append(f"very little text content ({words} words)")
    return int(clamp(pts, 0, 100)), notes

def _score_business(detected: dict) -> tuple[int, list[str]]:
    pts, notes = 0, []
    table = [("catalog", 20, "no online catalogue"), ("prices", 15, "no prices published"),
             ("online_order", 15, "no online ordering"), ("booking", 10, "no online booking"),
             ("whatsapp", 12, "no WhatsApp link"), ("contact_form", 13, "no contact form"),
             ("social_links", 8, "no social links"), ("multi_language", 7, "single language only")]
    for key, weight, miss in table:
        if detected.get(key):
            pts += weight
        else:
            notes.append(miss)
    return int(clamp(pts, 0, 100)), notes

WEIGHTS = {"technical": 0.20, "mobile": 0.25, "performance": 0.20, "ux": 0.20, "business": 0.15}

def analyze(url: str, *, provider: str = "website", cache_ttl_s: int = 3600) -> WebsiteAnalysis:
    """Fetch and analyse one site. Never raises for network problems."""
    normalized = nz.normalize_url(url)
    out = WebsiteAnalysis(url=normalized or (url or ""))
    if not normalized:
        out.error_code = "invalid_url"
        out.facts.append(Fact("website_url_invalid", "true", "input", None, 1.0))
        return out
    with obs.timed("analyzer.total_ms"):
        res = fetch(normalized, provider=provider, cache_ttl_s=cache_ttl_s)
        out.final_url = res.final_url
        out.http_status = res.status
        out.https = bool(res.https)
        out.ssl_valid = res.tls_ok
        out.redirects = res.redirects
        out.response_ms = res.elapsed_ms or None
        out.error_code = res.error_code
        out.html_bytes = len(res.body.encode("utf-8", errors="ignore")) if res.body else 0
        if not res.ok:
            out.reachable = False
            out.scores = {k: 0 for k in WEIGHTS}
            out.total_score = 0
            out.facts.append(Fact("website_reachable", "false", "http_check", normalized, 0.95))
            if res.error_code:
                out.facts.append(Fact("website_error", res.error_code, "http_check", normalized, 0.95))
            if res.status:
                out.facts.append(Fact("website_http_status", str(res.status), "http_check", normalized, 0.99))
            return out

        out.reachable = True
        html = res.body
        ex = htmlparse.extract(html)
        text = ex.text
        low_html = html.lower()

        socials = nz.extract_social(html)
        phones: list[str] = []
        for m in re.finditer(r"(?:\+?996|0)[\s\-()]?\d{3}[\s\-()]?\d{2}[\s\-()]?\d{2}[\s\-()]?\d{2}", html):
            p, conf = nz.normalize_phone(m.group(0))
            if p and p not in phones:
                phones.append(p)
        for href, _t in ex.links:
            if href.lower().startswith("tel:"):
                p, _ = nz.normalize_phone(href[4:])
                if p and p not in phones:
                    phones.append(p)
        emails: list[str] = []
        for m in re.finditer(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", html):
            e = nz.normalize_email(m.group(0))
            if e and e not in emails and not e.endswith((".png", ".jpg", ".webp")):
                emails.append(e)
        contacts = {"phone": phones[0] if phones else None, "phones": phones[:5],
                    "email": emails[0] if emails else None, "emails": emails[:5]}

        sitemap_ok = bool(ex.link_rels.get("sitemap")) or "sitemap" in low_html
        robots_ok = True
        catalog_hit = _contains_any(text, CATALOG_WORDS) or _contains_any(" ".join(h for h, _ in ex.links), CATALOG_WORDS)
        detected = {
            "catalog": bool(catalog_hit),
            "prices": any(p.search(text) for p in PRICE_PATTERNS),
            "online_order": bool(_contains_any(low_html, ORDER_WORDS)),
            "booking": bool(_contains_any(low_html, BOOKING_WORDS)),
            "whatsapp": bool(socials.get("whatsapp")) or "wa.me" in low_html or "whatsapp" in low_html,
            "contact_form": bool(ex.forms) and ex.inputs >= 2,
            "social_links": bool({k for k in socials if k != "whatsapp"}),
            "multi_language": sum(1 for hints in LANG_HINTS.values() if any(h in low_html for h in hints)) >= 2,
            "cta": bool(_contains_any(text, CTA_WORDS)),
            "viewport": bool(ex.metas.get("viewport")),
            "socials": socials,
            "contacts": contacts,
            "title": ex.title,
            "meta_description": ex.metas.get("description", ""),
            "lang": ex.lang,
            "word_count": len(text.split()),
            "forms": len(ex.forms),
            "scripts": ex.scripts,
            "images": ex.images,
        }

        tech, tech_notes = _score_technical(res, ex, sitemap_ok, robots_ok)
        mob, mob_notes = _score_mobile(ex, html)
        perf, perf_notes = _score_performance(res, ex, out.html_bytes)
        ux, ux_notes = _score_ux(ex, text, contacts)
        biz, biz_notes = _score_business(detected)
        out.scores = {"technical": tech, "mobile": mob, "performance": perf, "ux": ux, "business": biz}
        out.total_score = int(round(sum(out.scores[k] * w for k, w in WEIGHTS.items())))
        out.detected = detected
        out.detected["weaknesses"] = (mob_notes + ux_notes + biz_notes + perf_notes + tech_notes)[:12]

        f = out.facts
        f.append(Fact("website_reachable", "true", "http_check", res.final_url))
        f.append(Fact("website_http_status", str(res.status), "http_check", res.final_url))
        f.append(Fact("website_https", str(bool(res.https)).lower(), "http_check", res.final_url))
        if res.https:
            f.append(Fact("website_tls_valid", str(bool(res.tls_ok)).lower(), "tls_handshake", res.final_url))
        f.append(Fact("website_response_ms", str(res.elapsed_ms), "http_check", res.final_url, 0.9))
        f.append(Fact("website_mobile_viewport", str(detected["viewport"]).lower(), "html_meta", res.final_url))
        for key in ("catalog", "prices", "online_order", "booking", "whatsapp", "contact_form", "multi_language"):
            f.append(Fact(f"website_{key}", str(bool(detected[key])).lower(), "html_content", res.final_url, 0.85))
        if contacts["phone"]:
            f.append(Fact("phone_on_website", contacts["phone"], "html_content", res.final_url, 0.9))
        if contacts["email"]:
            f.append(Fact("email_on_website", contacts["email"], "html_content", res.final_url, 0.9))
        for net, handle in socials.items():
            f.append(Fact(f"{net}_on_website", handle, "html_content", res.final_url, 0.9))
    return out
