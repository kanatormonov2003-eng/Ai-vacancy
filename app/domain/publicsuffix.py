"""Public suffix resolution using the real PSL algorithm.

Why this module exists: the previous implementation inferred registrable domains
from a seven-entry hardcoded suffix set, so every unlisted two-level ccTLD
collapsed onto the suffix itself (`toyota.co.jp` and `sony.co.jp` both became
`co.jp`, giving unrelated companies the dedupe key `d:co.jp`). Guessing suffixes
from a small list cannot be made correct: the suffix set is data, not logic.

Design:

* Rules are loaded from a publicsuffix.org-format file
  (`data/public_suffix_list.dat`, overridable with `PUBLIC_SUFFIX_LIST_PATH`),
  so refreshing the list is an ops action, not a code change.
* Matching follows the published algorithm: `!` exception rules win, otherwise
  the longest matching rule wins, `*` matches exactly one label, and the default
  rule is `*` when nothing matches.
* Unlisted suffixes get one extra guard (`_UNKNOWN_SECOND_LEVEL`): under an
  unknown two-letter ccTLD a registry-style second label such as `com`/`co`/
  `gov` is treated as part of the suffix. Without it a missing rule would
  silently re-introduce the false-merge bug; with it the worst case is a
  slightly over-long suffix, which can only split leads apart, never merge
  strangers.
* Hosts are punycoded before matching so IDN input hits the ASCII rules.
"""
from __future__ import annotations
import ipaddress, os, threading

_DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "public_suffix_list.dat")

# Registry-style second labels. Only consulted when no PSL rule matched at all.
_UNKNOWN_SECOND_LEVEL = frozenset({
    "com", "co", "net", "org", "edu", "ac", "gov", "gob", "go", "govt", "mil", "int",
    "biz", "info", "name", "pro", "web", "sch", "school", "res", "or", "ne", "ad",
    "gr", "lg", "nom", "asso", "firm", "gen", "ind", "k12", "muni", "pvt", "pp", "of",
})


class _Rules:
    __slots__ = ("exact", "wildcard", "exception", "private", "source", "count")

    def __init__(self) -> None:
        self.exact: set[str] = set()
        self.wildcard: set[str] = set()
        self.exception: set[str] = set()
        self.private: set[str] = set()
        self.source = ""
        self.count = 0


_lock = threading.Lock()
_rules: _Rules | None = None


def _needs_idna(value: str) -> bool:
    return any(ord(ch) > 127 for ch in value)


def _parse(text: str, source: str) -> _Rules:
    parsed = _Rules()
    icann = True
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("//"):
            marker = line.upper()
            if "BEGIN PRIVATE DOMAINS" in marker:
                icann = False
            elif "BEGIN ICANN DOMAINS" in marker:
                icann = True
            continue
        if not line:
            continue
        line = line.split()[0].strip(".").lower()
        if not line:
            continue
        if _needs_idna(line):
            try:
                line = line.encode("idna").decode("ascii")
            except (UnicodeError, ValueError):
                continue
        if line.startswith("!"):
            parsed.exception.add(line[1:])
        elif line.startswith("*."):
            parsed.wildcard.add(line)
        else:
            parsed.exact.add(line)
        if not icann:
            parsed.private.add(line.lstrip("!"))
        parsed.count += 1
    parsed.source = source
    return parsed


def rules() -> _Rules:
    global _rules
    with _lock:
        if _rules is not None:
            return _rules
        path = os.environ.get("PUBLIC_SUFFIX_LIST_PATH") or _DATA_FILE
        with open(path, "r", encoding="utf-8") as fh:
            _rules = _parse(fh.read(), path)
        return _rules


def reload_rules() -> None:
    """Drop the cached rule set (tests, and an ops-triggered list refresh)."""
    global _rules
    with _lock:
        _rules = None


def normalize_host(host: str | None) -> str | None:
    """Lowercase, strip the root dot, punycode. None for unusable input."""
    if not host:
        return None
    value = str(host).strip().strip(".").lower()
    if not value or " " in value or ".." in value:
        return None
    if _needs_idna(value):
        labels = []
        for label in value.split("."):
            if not label:
                return None
            if _needs_idna(label):
                try:
                    label = label.encode("idna").decode("ascii")
                except (UnicodeError, ValueError):
                    return None
            labels.append(label)
        value = ".".join(labels)
    if not all(value.split(".")):
        return None
    return value


def is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def public_suffix(host: str | None) -> tuple[str | None, bool]:
    """Return (public suffix, matched_a_rule).

    `matched_a_rule` is False when the PSL default rule `*` was used, which is
    what callers need in order to apply the unknown-ccTLD guard.
    """
    normalized = normalize_host(host)
    if not normalized or is_ip(normalized):
        return None, False
    labels = normalized.split(".")
    rset = rules()
    if len(labels) == 1:
        return normalized, normalized in rset.exact
    for i in range(len(labels)):
        if ".".join(labels[i:]) in rset.exception:
            return ".".join(labels[i + 1:]), True
    for i in range(len(labels)):
        candidate = labels[i:]
        joined = ".".join(candidate)
        if joined in rset.exact:
            return joined, True
        if ".".join(["*"] + candidate[1:]) in rset.wildcard:
            return joined, True
    return labels[-1], False


def registrable_domain(host: str | None) -> str | None:
    """The company-owned domain: public suffix plus exactly one label.

    Returns None when the host *is* a public suffix, is an IP literal, or is
    unusable. Callers must never fall back to the suffix itself - that fallback
    is the false-merge bug this module replaces.
    """
    normalized = normalize_host(host)
    if not normalized or is_ip(normalized):
        return None
    suffix, matched = public_suffix(normalized)
    if not suffix:
        return None
    labels = normalized.split(".")
    suffix_len = suffix.count(".") + 1
    if not matched and len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _UNKNOWN_SECOND_LEVEL:
        suffix_len = 2
    if len(labels) <= suffix_len:
        return None
    return ".".join(labels[-(suffix_len + 1):])


def is_public_suffix(host: str | None) -> bool:
    normalized = normalize_host(host)
    if not normalized:
        return False
    suffix, matched = public_suffix(normalized)
    return bool(matched and suffix == normalized)


def info() -> dict:
    r = rules()
    return {"source": r.source, "rules": r.count, "exact": len(r.exact),
            "wildcard": len(r.wildcard), "exception": len(r.exception),
            "private": len(r.private)}
