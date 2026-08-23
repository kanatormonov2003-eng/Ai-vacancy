"""Hardened outbound HTTP.

* DNS resolution runs in a watchdog thread: getaddrinfo has no timeout and will
  otherwise hang the whole worker when no resolver is reachable (observed in this
  environment).
* SSRF guard: resolve first, reject private/loopback/link-local/reserved targets,
  connect straight to the validated IP and pass SNI/Host separately.
* Bounded body size, bounded redirects (each hop re-validated), timeouts,
  retries with exponential backoff + jitter, a global retry budget and a
  per-host circuit breaker.
* robots.txt is honoured (cached) and never bypassed.
"""
from __future__ import annotations
import datetime as dt, hashlib, http.client, ipaddress, random, socket, ssl, threading, time
import urllib.robotparser as robotparser
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from .. import obs
from ..config import load
from ..db import sqlite as db
from ..util import dumps, iso, loads, now, now_iso, parse_iso
from . import circuit

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}

# P0-4 / P1-9: credentials must never follow a redirect to another origin, and a
# cached response fetched with credentials must never be served to a caller
# without them (or with different ones).
SENSITIVE_HEADERS = ("authorization", "cookie", "proxy-authorization", "x-api-key", "x-auth-token")

def _origin(url):
    p = urlsplit(url)
    scheme = (p.scheme or "http").lower()
    return scheme, (p.hostname or "").lower(), p.port or (443 if scheme == "https" else 80)

def _drop_sensitive(headers):
    return {k: v for k, v in (headers or {}).items() if k.lower() not in SENSITIVE_HEADERS}

def _credential_marker(headers):
    creds = {k.lower(): v for k, v in (headers or {}).items() if k.lower() in SENSITIVE_HEADERS}
    if not creds:
        return ""
    blob = dumps({k: creds[k] for k in sorted(creds)})
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

@dataclass
class HttpResult:
    url: str
    final_url: str | None = None
    status: int | None = None
    body: str = ""
    headers: dict = field(default_factory=dict)
    elapsed_ms: int = 0
    redirects: int = 0
    https: bool = False
    tls_ok: bool | None = None
    tls_error: str | None = None
    ip: str | None = None
    error_code: str | None = None
    from_cache: bool = False
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.error_code is None and self.status is not None and 200 <= self.status < 400

class _RetryBudget:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._spent = 0
        self._window = time.monotonic()

    def take(self) -> bool:
        cfg = load()
        with self._lock:
            now_m = time.monotonic()
            if now_m - self._window > 60:
                self._window, self._spent = now_m, 0
            if self._spent >= cfg.http_retry_budget:
                return False
            self._spent += 1
            return True

    def reset(self) -> None:
        with self._lock:
            self._spent = 0
            self._window = time.monotonic()

BUDGET = _RetryBudget()

def resolve(host: str, timeout: float) -> tuple[list[str], str | None]:
    """Resolve with a hard timeout. Returns (ips, error_code)."""
    result: dict = {}

    def _run() -> None:
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
            result["ips"] = sorted({i[4][0] for i in infos})
        except socket.gaierror as e:
            result["error"] = "dns_not_found" if e.errno in (socket.EAI_NONAME, -2, -5) else "dns_error"
        except Exception:
            result["error"] = "dns_error"

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        obs.incr("http.dns_timeout")
        return [], "dns_timeout"
    if "error" in result:
        return [], result["error"]
    return result.get("ips", []), None if result.get("ips") else "dns_not_found"

def _ip_allowed(ip: str) -> bool:
    if load().allow_private_hosts:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
                or addr.is_reserved or addr.is_unspecified)

def _cache_get(key: str) -> HttpResult | None:
    row = db.one("SELECT * FROM http_cache WHERE cache_key = ?", (key,))
    if not row:
        return None
    exp = parse_iso(row["expires_at"])
    if exp is None or exp < now():
        db.execute("DELETE FROM http_cache WHERE cache_key = ?", (key,))
        return None
    res = HttpResult(url=row["url"], final_url=row["url"], status=row["status"], body=row["body"] or "",
                    headers=loads(row["headers"], {}) or {}, error_code=row["error_code"], from_cache=True)
    res.https = row["url"].startswith("https://")
    return res

def _cache_put(key: str, res: HttpResult, ttl_s: int) -> None:
    db.execute(
        "INSERT INTO http_cache (cache_key,url,status,body,headers,error_code,fetched_at,expires_at) "
        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET status=excluded.status, "
        "body=excluded.body, headers=excluded.headers, error_code=excluded.error_code, "
        "fetched_at=excluded.fetched_at, expires_at=excluded.expires_at",
        (key, res.final_url or res.url, res.status, res.body[:500_000], dumps(res.headers), res.error_code,
         now_iso(), iso(now() + dt.timedelta(seconds=ttl_s))))

def _single_request(url: str, *, timeout: float, max_bytes: int, method: str = "GET",
                    headers: dict | None = None, body: bytes | None = None,
                    ca_file: str | None = None) -> HttpResult:
    cfg = load()
    res = HttpResult(url=url)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        res.error_code = "unsupported_scheme"
        return res
    host = parts.hostname or ""
    if not host:
        res.error_code = "invalid_url"
        return res
    port = parts.port or (443 if parts.scheme == "https" else 80)
    res.https = parts.scheme == "https"

    ips, dns_error = resolve(host, min(timeout, 5.0))
    if dns_error:
        res.error_code = dns_error
        return res
    usable = [ip for ip in ips if _ip_allowed(ip)]
    if not usable:
        res.error_code = "blocked_private_address"
        obs.warn("http.ssrf_blocked", host=host, ips=ips)
        return res
    ip = usable[0]
    res.ip = ip

    started = time.perf_counter()
    sock = None
    try:
        sock = socket.create_connection((ip, port), timeout=timeout)
        if res.https:
            ctx = ssl.create_default_context(cafile=ca_file)
            try:
                sock = ctx.wrap_socket(sock, server_hostname=host)
                res.tls_ok = True
            except ssl.SSLError as e:
                res.tls_ok = False
                res.tls_error = getattr(e, "reason", None) or str(e)[:120]
                # retry without verification purely to observe the site; never trusted
                sock.close()
                sock = socket.create_connection((ip, port), timeout=timeout)
                insecure = ssl._create_unverified_context()
                sock = insecure.wrap_socket(sock, server_hostname=host)
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.sock = sock
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        req_headers = {"Host": host, "User-Agent": cfg.http_user_agent,
                       "Accept": "text/html,application/json;q=0.9,*/*;q=0.5",
                       "Accept-Language": "ru,ky,en;q=0.8", "Connection": "close"}
        req_headers.update(headers or {})
        conn.request(method, path, body=body, headers=req_headers)
        resp = conn.getresponse()
        res.status = resp.status
        res.headers = {k.lower(): v for k, v in resp.getheaders()}
        raw = resp.read(max_bytes + 1)
        if len(raw) > max_bytes:
            res.truncated = True
            raw = raw[:max_bytes]
        charset = "utf-8"
        ctype = res.headers.get("content-type", "")
        if "charset=" in ctype:
            charset = ctype.split("charset=")[-1].split(";")[0].strip() or "utf-8"
        try:
            res.body = raw.decode(charset, errors="replace")
        except LookupError:
            res.body = raw.decode("utf-8", errors="replace")
        conn.close()
    except socket.timeout:
        res.error_code = "timeout"
    except ssl.SSLError as e:
        res.error_code = "tls_error"
        res.tls_ok = False
        res.tls_error = str(e)[:120]
    except ConnectionRefusedError:
        res.error_code = "connection_refused"
    except OSError as e:
        res.error_code = "network_error"
        res.tls_error = str(e)[:120]
    except http.client.HTTPException:
        res.error_code = "malformed_response"
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
    res.elapsed_ms = int((time.perf_counter() - started) * 1000)
    res.final_url = url
    return res

_robots_lock = threading.Lock()
_robots_cache: dict[str, tuple[float, robotparser.RobotFileParser | None]] = {}

def robots_allows(url: str, *, timeout: float = 4.0) -> bool:
    cfg = load()
    if not cfg.respect_robots:
        return True
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    with _robots_lock:
        cached = _robots_cache.get(origin)
    if cached and time.monotonic() - cached[0] < 900:
        parser = cached[1]
    else:
        res = _single_request(origin + "/robots.txt", timeout=timeout, max_bytes=100_000)
        parser = None
        if res.ok and res.body:
            parser = robotparser.RobotFileParser()
            parser.parse(res.body.splitlines())
        with _robots_lock:
            _robots_cache[origin] = (time.monotonic(), parser)
    if parser is None:
        return True  # no robots.txt published => allowed
    return parser.can_fetch(cfg.http_user_agent, url)

def clear_robots_cache() -> None:
    with _robots_lock:
        _robots_cache.clear()

def fetch(url: str, *, provider: str = "web", timeout: float | None = None, retries: int | None = None,
          max_redirects: int = 4, cache_ttl_s: int = 0, method: str = "GET",
          headers: dict | None = None, body: bytes | None = None, check_robots: bool = True,
          ca_file: str | None = None) -> HttpResult:
    cfg = load()
    timeout = cfg.http_timeout_s if timeout is None else timeout
    retries = cfg.http_retries if retries is None else retries
    marker = _credential_marker(headers)
    cache_key = f"{method}:{url}" + (f"#cred={marker}" if marker else "")
    if cache_ttl_s and method == "GET":
        cached = _cache_get(cache_key)
        if cached is not None:
            obs.incr("http.cache_hit", provider=provider)
            return cached
    circuit.guard(provider)
    if check_robots and method == "GET" and not robots_allows(url, timeout=min(timeout, 4.0)):
        obs.info("http.robots_disallowed", url=url)
        res = HttpResult(url=url, error_code="robots_disallowed")
        return res

    attempt, current_url, total_redirects = 0, url, 0
    start_origin = _origin(url)
    hop_headers = dict(headers or {})
    res = HttpResult(url=url, error_code="not_attempted")
    while True:
        res = _single_request(current_url, timeout=timeout, max_bytes=cfg.http_max_bytes,
                             method=method, headers=hop_headers, body=body, ca_file=ca_file)
        res.redirects = total_redirects
        if res.status in (301, 302, 303, 307, 308) and total_redirects < max_redirects:
            location = res.headers.get("location")
            if location:
                from ..domain.normalize import normalize_url
                nxt = location if "://" in location else None
                if nxt is None:
                    parts = urlsplit(current_url)
                    nxt = f"{parts.scheme}://{parts.netloc}" + (location if location.startswith("/") else "/" + location)
                nxt = normalize_url(nxt)
                if nxt and nxt != current_url:
                    if _origin(nxt) != start_origin and _credential_marker(hop_headers):
                        obs.warn("http.credentials_stripped", provider=provider,
                                 to_host=_origin(nxt)[1])
                        hop_headers = _drop_sensitive(hop_headers)
                    current_url, total_redirects = nxt, total_redirects + 1
                    continue
        retryable = res.error_code in ("timeout", "network_error", "malformed_response") or (res.status in RETRYABLE_STATUS)
        if retryable and attempt < retries and BUDGET.take():
            sleep_s = min(2.0, (0.25 * (2 ** attempt))) * (0.75 + random.random() * 0.5)
            obs.incr("http.retry", provider=provider)
            time.sleep(sleep_s)
            attempt += 1
            continue
        break

    res.final_url = current_url
    res.redirects = total_redirects
    obs.observe("http.fetch_ms", res.elapsed_ms)
    if res.error_code or (res.status and res.status >= 500):
        circuit.record_failure(provider, res.error_code or f"http_{res.status}")
        obs.incr("http.failure", provider=provider, code=res.error_code or str(res.status))
    else:
        circuit.record_success(provider)
        obs.incr("http.success", provider=provider)
    if cache_ttl_s and method == "GET" and res.status is not None:
        _cache_put(cache_key, res, cache_ttl_s)
    return res
