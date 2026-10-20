"""Deterministic cleanup and conversion of public Help Center articles."""

import re
import unicodedata
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from markdownify import MarkdownConverter


class _ArticleConverter(MarkdownConverter):
    def convert_pre(self, el, text, parent_tags):
        code = el.get_text()
        longest = max((len(match.group()) for match in re.finditer(r"`+", code)), default=0)
        fence = "`" * max(3, longest + 1)
        language = ""
        code_element = el.find("code")
        if code_element:
            for class_name in code_element.get("class", []):
                if class_name.startswith("language-"):
                    language = class_name.removeprefix("language-")
                    break
        return f"\n\n{fence}{language}\n{code.rstrip()}\n{fence}\n\n"


def safe_filename(slug: str, article_id: str, title: str = "") -> str:
    """Return a stable ASCII filename with the article ID preventing collisions."""
    filename_slug = slug or title
    normalized = unicodedata.normalize("NFKD", filename_slug)
    ascii_slug = normalized.encode("ascii", "ignore").decode("ascii").lower()
    ascii_slug = re.sub(r"[^a-z0-9]+", "-", ascii_slug).strip("-") or "article"
    safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(article_id)).strip("-") or "unknown"
    return f"{ascii_slug[:120]}-{safe_id}.md"


def html_to_markdown(title: str, html: str, source_url: str) -> str:
    """Convert an article body while preserving useful content and source links."""
    soup = BeautifulSoup(html or "", "html.parser")
    for element in soup.select(
        "script, style, nav, aside, footer, form, "
        "[role='navigation'], [aria-hidden='true'], "
        ".advertisement, .ads, .promo, .cookie-banner, "
        ".cookie-consent, .breadcrumbs, .related-articles"
    ):
        element.decompose()

    article = (
        soup.select_one("article")
        or soup.select_one(".article-body")
        or soup.select_one(".article-content")
        or soup.body
        or soup
    )
    for link in article.find_all("a", href=True):
        link["href"] = urljoin(source_url, link["href"])
    for image in article.find_all("img", src=True):
        image["src"] = urljoin(source_url, image["src"])

    converted = _ArticleConverter(
        heading_style="ATX",
        bullets="-",
        strip=["script", "style"],
    ).convert(str(article)).strip()
    converted = re.sub(r"\n[ \t]+\n", "\n\n", converted)
    converted = re.sub(r"\n{3,}", "\n\n", converted)
    clean_title = re.sub(r"\s+", " ", title).strip() or "Untitled"
    canonical = source_url.strip()
    if not canonical:
        raise ValueError("A canonical source URL is required for each article.")
    return f"# {clean_title}\nArticle URL: {canonical}\n\n{converted}\n"


def write_article(
    output_dir: Path,
    article_id: str,
    slug: str,
    markdown: str,
    title: str = "",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / safe_filename(slug, article_id, title)
    path.write_text(markdown, encoding="utf-8", newline="\n")
    return path
