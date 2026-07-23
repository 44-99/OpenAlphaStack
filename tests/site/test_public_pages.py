from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "site"
PUBLIC_PAGES = ("index.html", "en.html", "privacy.html", "terms.html", "support.html")


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in {"a", "link", "script", "img"}:
            return
        values = dict(attrs)
        target = values.get("href") or values.get("src")
        if target:
            self.links.append(target)


def test_public_pages_have_no_missing_local_links():
    for name in PUBLIC_PAGES:
        path = SITE / name
        parser = LinkParser()
        parser.feed(path.read_text(encoding="utf-8"))
        for target in parser.links:
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or target.startswith("#"):
                continue
            local = parsed.path or "index.html"
            if local in {".", "./"}:
                local = "index.html"
            candidate = SITE / local
            if local.startswith("assets/"):
                candidate = ROOT / "docs" / local
            assert candidate.exists(), f"{name} references missing {target}"


def test_sitemap_lists_policy_and_support_pages():
    tree = ElementTree.parse(SITE / "sitemap.xml")
    namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = {node.text for node in tree.findall("s:url/s:loc", namespace)}

    assert "https://44-99.github.io/OpenAlphaStack/privacy.html" in urls
    assert "https://44-99.github.io/OpenAlphaStack/terms.html" in urls
    assert "https://44-99.github.io/OpenAlphaStack/support.html" in urls


def test_policy_pages_name_the_public_mcp_boundary():
    privacy = (SITE / "privacy.html").read_text(encoding="utf-8")
    terms = (SITE / "terms.html").read_text(encoding="utf-8")
    support = (SITE / "support.html").read_text(encoding="utf-8")

    assert "公网 MCP" in privacy and "不读取或保存本地模拟盘" in privacy
    assert "不接入券商" in terms and "不执行订单" in terms
    assert "/health" in support and "Security Policy" in support
