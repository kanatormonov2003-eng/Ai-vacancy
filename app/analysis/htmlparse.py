"""Zero-dependency tolerant HTML extraction (stdlib html.parser).

Deliberately not a full DOM: we only need the deterministic signals the scorer
uses. Malformed markup must never raise.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

SKIP_TEXT_TAGS = {"script", "style", "noscript", "template", "svg"}

@dataclass
class Extracted:
    title: str = ""
    lang: str = ""
    charset: str = ""
    metas: dict = field(default_factory=dict)
    links: list[tuple[str, str]] = field(default_factory=list)   # (href, text)
    link_rels: dict = field(default_factory=dict)
    scripts: int = 0
    stylesheets: int = 0
    inline_styles: int = 0
    images: int = 0
    images_responsive: int = 0
    forms: list[dict] = field(default_factory=list)
    inputs: int = 0
    headings: dict = field(default_factory=dict)
    text: str = ""
    tables: int = 0
    nav_elements: int = 0
    buttons: int = 0
    iframes: int = 0
    fixed_widths: list[int] = field(default_factory=list)
    has_flash: bool = False
    has_picture: bool = False
    tag_counts: dict = field(default_factory=dict)
    truncated_markup: bool = False

class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out = Extracted()
        self._stack: list[str] = []
        self._in_title = False
        self._text_parts: list[str] = []
        self._current_link: list | None = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        a = {k.lower(): (v or "") for k, v in attrs}
        o = self.out
        o.tag_counts[tag] = o.tag_counts.get(tag, 0) + 1
        self._stack.append(tag)
        if tag == "html" and a.get("lang"):
            o.lang = a["lang"][:20]
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name:
                o.metas[name] = a.get("content", "")[:500]
            if a.get("charset"):
                o.charset = a["charset"][:40]
            if (a.get("http-equiv") or "").lower() == "content-type" and "charset=" in a.get("content", "").lower():
                o.charset = a["content"].lower().split("charset=")[-1][:40]
        elif tag == "title":
            self._in_title = True
        elif tag == "a":
            self._current_link = [a.get("href", ""), []]
        elif tag == "link":
            rel = (a.get("rel") or "").lower()
            if rel:
                o.link_rels.setdefault(rel, a.get("href", ""))
            if "stylesheet" in rel:
                o.stylesheets += 1
        elif tag == "script":
            o.scripts += 1
        elif tag == "img":
            o.images += 1
            if a.get("srcset") or a.get("sizes") or "max-width" in (a.get("style") or "") or a.get("loading"):
                o.images_responsive += 1
            w = a.get("width", "")
            if w.isdigit() and int(w) > 700:
                o.fixed_widths.append(int(w))
        elif tag == "picture":
            o.has_picture = True
        elif tag == "form":
            o.forms.append({"action": a.get("action", ""), "method": (a.get("method") or "get").lower()})
        elif tag in ("input", "textarea", "select"):
            o.inputs += 1
        elif tag in ("h1", "h2", "h3"):
            o.headings[tag] = o.headings.get(tag, 0) + 1
        elif tag == "table":
            o.tables += 1
        elif tag == "nav":
            o.nav_elements += 1
        elif tag == "button":
            o.buttons += 1
        elif tag == "iframe":
            o.iframes += 1
        elif tag in ("embed", "object") and "flash" in (a.get("type", "") + a.get("src", "")).lower():
            o.has_flash = True
        style = a.get("style", "")
        if style:
            o.inline_styles += 1
            for m in re.finditer(r"width\s*:\s*(\d{3,5})px", style):
                if int(m.group(1)) > 700:
                    o.fixed_widths.append(int(m.group(1)))
        if tag == "table":
            w = a.get("width", "")
            if w.isdigit() and int(w) > 700:
                o.fixed_widths.append(int(w))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._current_link is not None:
            href, parts = self._current_link
            self.out.links.append((href, " ".join(parts).strip()[:120]))
            self._current_link = None
        while self._stack:
            popped = self._stack.pop()
            if popped == tag:
                break

    def handle_data(self, data):
        if self._in_title:
            self.out.title = (self.out.title + data).strip()[:300]
            return
        if self._stack and self._stack[-1] in SKIP_TEXT_TAGS:
            if self._stack[-1] == "style":
                self.out.text += ""
            return
        cleaned = data.strip()
        if cleaned:
            self._text_parts.append(cleaned)
            if self._current_link is not None:
                self._current_link[1].append(cleaned)

    def finish(self) -> Extracted:
        if self._current_link is not None:
            self.out.links.append((self._current_link[0], " ".join(self._current_link[1])[:120]))
        self.out.text = re.sub(r"\s+", " ", " ".join(self._text_parts))[:200_000]
        return self.out

def extract(html: str) -> Extracted:
    parser = _Parser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:  # tolerant by design: malformed markup must not raise
        parser.out.truncated_markup = True
    return parser.finish()
