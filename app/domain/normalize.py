"""Data normalisation for the Kyrgyz market: Cyrillic/Latin names, +996 phones, domains.

Identity keys generated here drive deduplication, so the bias is deliberately
towards *under*-matching. A key that is too specific costs a duplicate row; a key
that is too aggressive silently merges two real companies and loses data that
cannot be recovered.
"""
from __future__ import annotations
import difflib, re, unicodedata
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from . import publicsuffix

# Legal forms only. Words like "центр", "компания", "группа" are part of real
# trading names ("Центр Красоты" is not the same business as "Красоты"), so they
# are NOT stripped any more.
LEGAL_FORMS = {
    "осоо", "оао", "зао", "пао", "ооо", "оюу", "жчк", "ипп", "ип", "чп", "кфх", "мкк", "мкб",
    "llc", "ltd", "limited", "inc", "llp", "jsc", "corp", "gmbh", "co",
}
# Country-level noise only. City names are identity-bearing: "Бишкек Строй" and
# "Строй" are different companies, so cities must never be stripped.
NOISE_TOKENS = {"kg", "kyrgyzstan", "kyrgyz", "кыргызстан", "кыргыз", "киргизия"}

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "ң": "ng", "ө": "o", "ү": "u", "і": "i",
}

CITY_ALIASES = {
    "бишкек": "Бишкек", "bishkek": "Бишкек", "biskek": "Бишкек", "фрунзе": "Бишкек",
    "ош": "Ош", "osh": "Ош", "oш": "Ош",
    "джалал-абад": "Джалал-Абад", "жалал-абад": "Джалал-Абад", "jalal-abad": "Джалал-Абад",
    "каракол": "Каракол", "karakol": "Каракол",
    "токмок": "Токмок", "tokmok": "Токмок",
    "кара-балта": "Кара-Балта", "нарын": "Нарын", "naryn": "Нарын",
    "талас": "Талас", "talas": "Талас", "баткен": "Баткен", "batken": "Баткен",
    "кызыл-кия": "Кызыл-Кия", "узген": "Узген", "өзгөн": "Узген",
}
CITY_REGION = {
    "Бишкек": "Чуйская", "Токмок": "Чуйская", "Кара-Балта": "Чуйская",
    "Ош": "Ошская", "Узген": "Ошская", "Кызыл-Кия": "Баткенская",
    "Джалал-Абад": "Джалал-Абадская", "Каракол": "Иссык-Кульская",
    "Нарын": "Нарынская", "Талас": "Таласская", "Баткен": "Баткенская",
}


def transliterate(text: str) -> str:
    return "".join(_TRANSLIT.get(ch, ch) for ch in text.lower())


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = text.replace("«", " ").replace("»", " ").replace("\u201c", " ").replace("\u201d", " ")
    text = re.sub(r"[\"'`’]", " ", text)
    text = re.sub(r"[^\w\s\-]", " ", text, flags=re.UNICODE)
    text = re.sub(r"[\-_]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def company_display_name(raw: str) -> str:
    name = unicodedata.normalize("NFKC", (raw or "")).strip()
    name = re.sub(r"\s+", " ", name)
    return name[:200]


def name_tokens(raw: str) -> list[str]:
    """Order-preserving transliterated tokens of a company name."""
    folded = _fold(raw)
    if not folded:
        return []
    tokens = [t for t in folded.split() if t]
    meaningful = [t for t in tokens if t not in LEGAL_FORMS and t not in NOISE_TOKENS]
    if not meaningful:  # the name was nothing but a legal form / country noise
        meaningful = tokens
    out = []
    for token in meaningful:
        latin = re.sub(r"[^a-z0-9]", "", transliterate(token))
        if latin:
            out.append(latin)
    return out


def normalize_company_name(raw: str) -> str:
    """Matching key. ORDER IS SIGNIFICANT.

    The previous version sorted tokens, which made every anagram of a name
    collide: "Восток Строй" and "Строй Восток" produced one key and merged two
    unrelated builders. Token order is cheap evidence and it is kept.
    """
    tokens = name_tokens(raw)
    return " ".join(tokens) if len(tokens) > 1 else "".join(tokens)


def sorted_name_key(raw: str) -> str:
    """Order-insensitive key. A *candidate lookup* aid only - never an identity."""
    return " ".join(sorted(name_tokens(raw)))


def is_token_permutation(a: str, b: str) -> bool:
    """Same tokens, different order: a review case, never an automatic merge."""
    ta, tb = name_tokens(a), name_tokens(b)
    if len(ta) < 2 or len(tb) < 2:
        return False
    return ta != tb and sorted(ta) == sorted(tb)


def name_similarity(a: str, b: str) -> float:
    """Order-aware similarity in [0, 1].

    Pure permutations are capped below the merge threshold on purpose: shared
    vocabulary is weak evidence, identical wording in the same order is strong.
    """
    na, nb = normalize_company_name(a), normalize_company_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    ta, tb = name_tokens(a), name_tokens(b)
    sa, sb = set(ta), set(tb)
    jaccard = len(sa & sb) / len(sa | sb) if (sa | sb) else 0.0
    seq = difflib.SequenceMatcher(None, ta, tb).ratio()
    contained = 0.0
    if len(ta) >= 2 and len(tb) >= 2:
        shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        if any(longer[i:i + len(shorter)] == shorter for i in range(len(longer) - len(shorter) + 1)):
            contained = 0.9  # "Альфа Строй" inside "Группа Альфа Строй": contiguous, ordered
    score = max(0.45 * ratio + 0.30 * seq + 0.25 * jaccard, contained)
    if is_token_permutation(a, b):
        score = min(score, 0.62)  # below REVIEW_THRESHOLD-with-corroboration, never auto-merge
    return round(min(score, 0.99), 4)


# ------------------------------------------------------------------ phones
#
# Confidence contract (used by dedupe and by the API):
#   >= PHONE_STRONG_CONFIDENCE  validated national plan match -> may be an identity
#   0.5 .. 0.9                  syntactically valid E.164 for a known country,
#                               but unverifiable shape -> corroboration required
#   None                        not a phone number at all
#
# A digit run is never accepted just because it is 11-13 digits long: that rule
# turned tracking IDs into contact data and, worse, into dedupe keys.
PHONE_STRONG_CONFIDENCE = 0.9

KG_CC = "996"
MOBILE_PREFIXES = ("20", "22", "31", "50", "51", "55", "56", "57", "70", "75", "77", "88", "90", "91", "92", "99")
KG_LANDLINE_AREA = ("312", "3132", "3134", "3138", "3222", "3230", "3232", "3422", "3452",
                    "3522", "3534", "3622", "3652", "3722", "3733", "3746", "3922", "3946")

# National significant number length ranges per country calling code.
COUNTRY_NSN_LEN = {
    "1": (10, 10), "7": (10, 10), "20": (8, 10), "27": (9, 9), "30": (10, 10),
    "31": (9, 9), "32": (8, 9), "33": (9, 9), "34": (9, 9), "36": (8, 9),
    "39": (6, 11), "40": (9, 9), "41": (9, 9), "43": (7, 13), "44": (9, 10),
    "45": (8, 8), "46": (7, 13), "47": (8, 8), "48": (9, 9), "49": (6, 11),
    "60": (7, 9), "61": (9, 9), "62": (8, 12), "63": (10, 10), "64": (8, 10),
    "65": (8, 8), "66": (8, 9), "81": (9, 10), "82": (8, 10), "84": (9, 10),
    "86": (10, 11), "90": (10, 10), "91": (10, 10), "92": (9, 10), "93": (9, 9),
    "94": (9, 9), "95": (8, 10), "98": (10, 10), "212": (9, 9), "213": (9, 9),
    "216": (8, 8), "351": (9, 9), "352": (6, 9), "353": (7, 9), "354": (7, 9),
    "358": (6, 12), "359": (8, 9), "370": (8, 8), "371": (8, 8), "372": (7, 8),
    "373": (8, 8), "374": (8, 8), "375": (9, 9), "380": (9, 9), "381": (8, 9),
    "420": (9, 9), "421": (9, 9), "852": (8, 8), "853": (8, 8), "886": (9, 9),
    "961": (7, 8), "962": (8, 9), "965": (8, 8), "966": (9, 9), "968": (8, 8),
    "971": (8, 9), "972": (8, 9), "973": (8, 8), "974": (8, 8), "975": (7, 8),
    "976": (8, 8), "977": (9, 10), "992": (9, 9), "993": (8, 8), "994": (9, 9),
    "995": (9, 9), "996": (9, 9), "998": (9, 9),
}
_CC_LENGTHS = sorted({len(cc) for cc in COUNTRY_NSN_LEN}, reverse=True)


def _kg_confidence(national: str) -> float:
    if national.startswith(KG_LANDLINE_AREA):
        return 0.95
    if national[:2] in MOBILE_PREFIXES:
        return 0.95
    return 0.75  # plausible 9-digit KG number, prefix not in the published plan


def _digits_of(raw: str) -> str:
    text = unicodedata.normalize("NFKC", str(raw))
    text = text.replace("(", " ").replace(")", " ").replace("-", "").replace("\u00a0", " ")
    # only the first phone-looking run: "0555 11 22 33, 0700 44 55 66" -> the first
    text = re.split(r"[,;/]|\sили\s|\sor\s|\bext\b|доб\.", text)[0]
    return re.sub(r"\D", "", text)


def normalize_phone(raw: str | None, default_country: str = KG_CC) -> tuple[str | None, float]:
    """Return (E.164 phone or None, confidence in [0, 1])."""
    if not raw:
        return None, 0.0
    digits = _digits_of(raw)
    if not digits or len(digits) > 15:
        return None, 0.0
    if digits.startswith("00"):
        digits = digits[2:]
    if not digits:
        return None, 0.0

    # local Kyrgyz forms first
    national = None
    if digits.startswith(default_country) and len(digits) == len(default_country) + 9:
        national = digits[len(default_country):]
    elif len(digits) == 10 and digits.startswith("0"):
        national = digits[1:]
    elif len(digits) == 9:
        national = digits
    if national is not None:
        if len(national) != 9 or not national.isdigit() or national[0] == "0":
            return None, 0.0
        return "+" + default_country + national, _kg_confidence(national)

    # international: the country code must exist and the national number length
    # must match that country's plan. No plan match, no phone number.
    for size in _CC_LENGTHS:
        cc = digits[:size]
        bounds = COUNTRY_NSN_LEN.get(cc)
        if not bounds:
            continue
        rest = digits[size:]
        if not rest or rest[0] == "0":
            continue
        if bounds[0] <= len(rest) <= bounds[1]:
            # Real number for a real plan, but nothing here proves it belongs to
            # this company, so it stays below the strong-identity threshold.
            return "+" + digits, 0.8
    return None, 0.0


def is_mobile_kg(e164: str | None) -> bool:
    if not e164 or not e164.startswith("+" + KG_CC) or len(e164) != 13:
        return False
    national = e164[4:]
    return national[:2] in MOBILE_PREFIXES and not national.startswith(KG_LANDLINE_AREA)


def phone_kind(e164: str | None) -> str:
    if not e164:
        return "unknown"
    if not e164.startswith("+" + KG_CC):
        return "foreign"
    return "mobile" if is_mobile_kg(e164) else "landline"


# ------------------------------------------------------------------ urls
TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                   "fbclid", "gclid", "yclid", "ysclid", "msclkid", "igshid", "mc_cid", "mc_eid",
                   "_ga", "ref", "referrer"}


def normalize_url(raw: str | None) -> str | None:
    if not raw:
        return None
    text = str(raw).strip().strip("<>\"'")
    if not text or " " in text.strip():
        text = text.split()[0] if text.split() else ""
    if not text:
        return None
    if "://" not in text:
        text = "https:" + text if text.startswith("//") else "https://" + text
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    try:
        host = (parts.hostname or "").strip(".").lower()
    except ValueError:
        return None
    if not host or ("." not in host and host != "localhost"):
        return None
    if not publicsuffix.is_ip(host):
        try:
            host = host.encode("idna").decode("ascii")
        except (UnicodeError, UnicodeDecodeError):
            pass
    netloc = host
    try:
        port = parts.port
    except ValueError:
        return None
    if port and not ((parts.scheme == "http" and port == 80) or (parts.scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if k.lower() not in TRACKING_PARAMS])
    # NOTE: the trailing slash is preserved on purpose. Stripping it changes
    # robots.txt matching ("Disallow: /blocked/") and can change server routing.
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    return urlunsplit((parts.scheme, netloc, path or "/", query, ""))


def normalize_domain(raw: str | None) -> str | None:
    """Host of a URL, www-stripped. Not an identity: see registrable_domain()."""
    url = normalize_url(raw)
    if not url:
        return None
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return None
    host = host.lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host or None


def registrable_domain(raw: str | None) -> str | None:
    """Company-owned domain via the public suffix list.

    Returns None for IP literals, bare public suffixes and shared hosting
    suffixes, so those can never become a dedupe identity.
    """
    host = normalize_domain(raw)
    if not host:
        return None
    return publicsuffix.registrable_domain(host)


def same_registrable_domain(a: str | None, b: str | None) -> bool:
    da, dbv = registrable_domain(a), registrable_domain(b)
    return bool(da and dbv and da == dbv)


# ------------------------------------------------------------------ social
#
# Only real, public profile URLs count. The old version regex-matched the first
# path segment after a network host, so an embedded SDK or an OAuth dialog became
# a "social identity": facebook.com/v2.0/dialog/oauth -> {'facebook': 'v2.0'} and
# instagram.com/embed.js -> {'instagram': 'embed.js'}. Every site embedding the
# same widget then looked like the same company and merged at 0.95 confidence.
#
# Three independent gates, all of which must pass:
#   1. the host is an exact known profile host (SDK/CDN/API hosts are not listed)
#   2. the path is a profile path (reserved words, versions, assets rejected)
#   3. the handle matches that network's published handle grammar
SOCIAL_HOSTS = {
    "instagram": {"instagram.com", "www.instagram.com", "instagr.am", "www.instagr.am"},
    "facebook": {"facebook.com", "www.facebook.com", "web.facebook.com", "m.facebook.com",
                 "fb.com", "www.fb.com", "fb.me"},
    "telegram": {"t.me", "www.t.me", "telegram.me", "www.telegram.me"},
    "whatsapp": {"wa.me", "api.whatsapp.com", "web.whatsapp.com", "whatsapp.com",
                 "www.whatsapp.com", "chat.whatsapp.com"},
}
_HOST_TO_NETWORK = {host: net for net, hosts in SOCIAL_HOSTS.items() for host in hosts}

_COMMON_RESERVED = {
    "sdk", "sdks", "embed", "embeds", "embed.js", "oauth", "oauth2", "dialog", "dialogs",
    "share", "sharer", "share.php", "plugins", "plugin", "widget", "widgets", "tr", "pixel",
    "login", "logout", "signup", "signin", "register", "recover", "security", "checkpoint",
    "help", "support", "about", "privacy", "terms", "policies", "policy", "legal", "cookie",
    "developer", "developers", "business", "api", "apis", "graph", "static", "assets",
    "asset", "cdn", "images", "img", "media", "js", "css", "fonts", "download", "downloads",
    "ads", "ad", "adsmanager", "campaign", "tracking", "track", "events", "event", "connect",
    "search", "explore", "directory", "accounts", "account", "settings", "translate",
    "apps", "app", "web", "mobile", "lite", "careers", "jobs", "press", "brand", "blog",
    "permalink", "permalink.php", "hashtag", "watch", "video", "videos", "photo", "photos",
    "story", "stories", "reel", "reels", "tv", "live", "gaming", "marketplace", "notes",
    "groups", "group", "pages", "page", "public", "people", "profile", "home", "index",
    "index.php", "index.html", "r.php", "l.php", "n", "l", "p", "c", "s", "i", "e",
}
_NETWORK_RESERVED = {
    "telegram": {"joinchat", "iv", "addstickers", "addtheme", "setlanguage", "confirmphone",
                 "socks", "proxy", "bg", "contact", "auth", "faq", "tos", "apps", "share"},
    "facebook": {"profile.php", "pg", "pgs", "bookmarks", "campaign_landing", "flx", "gr"},
    "instagram": {"challenge", "emails", "legal", "developer", "web", "graphql", "api"},
    "whatsapp": {"send", "channel", "invite"},
}
_ASSET_SUFFIX = re.compile(r"\.(?:js|mjs|json|css|map|png|jpe?g|gif|svg|webp|ico|bmp|xml|txt|pdf|woff2?|ttf|eot|mp4|webm|html?|aspx?|php|cgi)$", re.I)
_VERSION_SEGMENT = re.compile(r"^v?\d+(?:[._]\d+)*$")
_OAUTH_PARAMS = {"client_id", "app_id", "redirect_uri", "response_type", "scope", "state",
                 "access_token", "api_key", "code", "next", "u", "text", "quote", "url",
                 "caption", "picture", "display", "sdk", "version", "ref", "return_uri"}

_HANDLE_RE = {
    "instagram": re.compile(r"^(?=.*[a-z0-9])[a-z0-9._]{2,30}$", re.I),
    "facebook": re.compile(r"^(?=.*[a-z])[a-z0-9.]{5,50}$", re.I),
    "telegram": re.compile(r"^(?=.*[a-z])[a-z0-9_]{5,32}$", re.I),
}
_URL_CANDIDATE = re.compile(
    r"(?<![\w.\-@])(?P<host>[a-z0-9][a-z0-9.\-]{0,60}\.[a-z]{2,10})(?P<path>/[^\s\"'<>\\\]),]*)?",
    re.I)
_SCRIPT_BLOCK = re.compile(r"<script\b.*?</script\s*>|<script\b[^>]*/?>", re.I | re.S)
_STYLE_BLOCK = re.compile(r"<style\b.*?</style\s*>", re.I | re.S)
_SRC_ATTR = re.compile(r"\b(?:src|data-src|srcset|integrity|content-security-policy)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)


def _strip_machine_urls(text: str) -> str:
    """Drop script/style bodies and src-like attributes.

    A network URL that only ever appears in a <script src> or a CSP header is
    infrastructure, not a company profile.
    """
    cleaned = _SCRIPT_BLOCK.sub(" ", text)
    cleaned = _STYLE_BLOCK.sub(" ", cleaned)
    return _SRC_ATTR.sub(" ", cleaned)


def _social_from_path(network: str, path: str) -> str | None:
    raw_path = path or "/"
    query = ""
    if "?" in raw_path:
        raw_path, query = raw_path.split("?", 1)
    raw_path = raw_path.split("#", 1)[0]
    params = {k.lower() for k, _v in parse_qsl(query, keep_blank_values=True)}

    if network == "whatsapp":
        digits = re.sub(r"\D", "", raw_path)
        if not digits:
            for key, value in parse_qsl(query, keep_blank_values=True):
                if key.lower() == "phone":
                    digits = re.sub(r"\D", "", value)
                    break
        if not digits:
            return None
        phone, confidence = normalize_phone(digits)
        return phone if phone and confidence >= 0.5 else None

    if params & _OAUTH_PARAMS:
        return None  # OAuth dialog / share intent / tracking call, not a profile

    segments = [s for s in raw_path.split("/") if s]
    if not segments:
        return None

    if network == "facebook" and segments[0].lower() == "profile.php":
        for key, value in parse_qsl(query, keep_blank_values=True):
            if key.lower() == "id" and value.isdigit() and 5 <= len(value) <= 25:
                return "profile.php?id=" + value
        return None
    if network == "facebook" and segments[0].lower() in ("pages", "pg") and len(segments) >= 2:
        segments = segments[1:]
    if network == "telegram" and segments[0].lower() == "s" and len(segments) >= 2:
        segments = segments[1:]

    handle = segments[0]
    if len(segments) > 2:
        return None  # deep paths are content or API routes, not profile roots
    if len(segments) == 2 and network in ("instagram", "telegram"):
        return None  # /<handle>/<post-id> style content links
    if handle.startswith("+") or handle.startswith("%2B"):
        return None  # telegram invite links identify a chat, not a public account
    low = handle.lower()
    if low in _COMMON_RESERVED or low in _NETWORK_RESERVED.get(network, set()):
        return None
    if _VERSION_SEGMENT.match(low) or _ASSET_SUFFIX.search(low):
        return None
    pattern = _HANDLE_RE.get(network)
    if pattern and not pattern.match(handle):
        return None
    return low.strip(".")


def social_profiles(text: str | None) -> list[dict]:
    """Every validated public profile URL found, with the network and handle."""
    if not text:
        return []
    cleaned = _strip_machine_urls(str(text))
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for match in _URL_CANDIDATE.finditer(cleaned):
        host = match.group("host").lower().strip(".")
        network = _HOST_TO_NETWORK.get(host)
        if not network:
            continue
        handle = _social_from_path(network, match.group("path") or "/")
        if not handle:
            continue
        key = (network, handle)
        if key in seen:
            continue
        seen.add(key)
        out.append({"network": network, "handle": handle, "url": match.group(0)})
    return out


def extract_social(text: str | None) -> dict[str, str]:
    """First validated public profile per network. {} when nothing qualifies."""
    out: dict[str, str] = {}
    for profile in social_profiles(text):
        out.setdefault(profile["network"], profile["handle"])
    return out


def social_handle(network: str, value: str | None) -> str | None:
    """Validate a bare handle supplied by a source ("@cafe.alfa" -> "cafe.alfa")."""
    if not value:
        return None
    raw = str(value).strip()
    if "/" in raw or "://" in raw or "." in raw and network == "telegram":
        found = extract_social(raw).get(network)
        return found
    handle = raw.lstrip("@").strip().lower()
    if not handle or handle in _COMMON_RESERVED or handle in _NETWORK_RESERVED.get(network, set()):
        return None
    pattern = _HANDLE_RE.get(network)
    if pattern and not pattern.match(handle):
        return None
    return handle


# ------------------------------------------------------------------ misc
def normalize_city(raw: str | None) -> tuple[str | None, str | None]:
    if not raw:
        return None, None
    key = _fold(raw).replace(" ", "-")
    city = CITY_ALIASES.get(key) or CITY_ALIASES.get(_fold(raw))
    if not city:
        for token in _fold(raw).split():
            if token in CITY_ALIASES:
                city = CITY_ALIASES[token]
                break
    if not city:
        return company_display_name(raw), None
    return city, CITY_REGION.get(city)


def address_similarity(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    fa, fb = _fold(a), _fold(b)
    ta = {t for t in fa.split() if len(t) > 2}
    tb = {t for t in fb.split() if len(t) > 2}
    if not ta or not tb:
        return 0.0
    return round(len(ta & tb) / len(ta | tb), 4)


# Deliberately permissive on the domain side: multi-label domains are normal
# (user@sub.example.com, a@mail.co.uk, a@b.gov.kg) and the previous validator
# rejected every one of them, locking real users out of registration.
_EMAIL_ATOM = r"[a-z0-9!#$%&*+/=?^_~-]+"
_EMAIL_LOCAL = _EMAIL_ATOM + r"(?:[.]" + _EMAIL_ATOM + r")*"
_EMAIL_DOMAIN = r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?[.])+[a-z]{2,63}"
_EMAIL_RE = re.compile(_EMAIL_LOCAL + "@" + _EMAIL_DOMAIN, re.I)


def normalize_email(raw: str | None) -> str | None:
    """Find and canonicalise the first email address inside free text."""
    if not raw:
        return None
    text = str(raw).strip().lower()
    match = _EMAIL_RE.search(text)
    if not match:
        return None
    email = match.group(0)
    if len(email) > 254 or ("." * 2) in email:
        return None
    local, _rest, domain = email.partition("@")
    if len(local) > 64 or domain.startswith(".") or domain.endswith("."):
        return None
    return email


def is_valid_email(raw: str | None) -> bool:
    """Whole-string validity. normalize_email searches, this one validates."""
    if not raw:
        return False
    text = str(raw).strip().lower()
    if len(text) > 254 or ("." * 2) in text or text.count("@") != 1:
        return False
    if not _EMAIL_RE.fullmatch(text):
        return False
    local, _rest, domain = text.partition("@")
    if len(local) > 64:
        return False
    return all(0 < len(label) <= 63 for label in domain.split("."))
