"""Public Zendesk Help Center API client."""

from dataclasses import dataclass
import logging
from typing import Iterator
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    slug: str
    body: str
    url: str
    locale: str
    updated_at: str


class ZendeskClient:
    """Fetch published public articles without Help Center credentials."""

    def __init__(
        self, base_url: str, locale: str, article_limit: int | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.locale = locale
        self.article_limit = article_limit
        self.session = session or requests.Session()
        retry = Retry(
            total=4,
            connect=4,
            read=4,
            status=4,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.mount("http://", HTTPAdapter(max_retries=retry))
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "OptiBotKnowledgeSync/1.0",
        })

    def _get_json(self, url: str, *, params: dict | None = None) -> dict:
        response = self.session.get(url, params=params, timeout=(10, 45))
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Zendesk returned invalid JSON from {url}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"Zendesk returned an invalid response from {url}")
        return data

    def _fetch_detail(self, article_id: str) -> dict | None:
        url = (
            f"{self.base_url}/api/v2/help_center/{self.locale}/"
            f"articles/{article_id}.json"
        )
        try:
            return self._get_json(url).get("article")
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (403, 404):
                logger.info("Article %s is not publicly accessible; skipping", article_id)
                return None
            raise

    def list_articles(self) -> Iterator[Article]:
        """Yield unique, published articles, following the API's next-page links."""
        url = (
            f"{self.base_url}/api/v2/help_center/"
            f"{self.locale}/articles.json"
        )
        params: dict | None = {"per_page": 100}
        seen: set[str] = set()
        visited_pages: set[str] = set()
        yielded = 0
        while url:
            url = urljoin(self.base_url + "/", url)
            if url in visited_pages:
                raise RuntimeError("Zendesk pagination returned a repeated page URL.")
            visited_pages.add(url)
            parsed = urlparse(url)
            if parsed.netloc != urlparse(self.base_url).netloc:
                raise RuntimeError("Zendesk pagination returned an unexpected host.")
            payload = self._get_json(url, params=params)
            records = payload.get("articles", [])
            if not isinstance(records, list):
                raise RuntimeError("Zendesk response has an invalid articles collection.")

            for summary in records:
                if not isinstance(summary, dict) or summary.get("id") is None:
                    continue
                article_id = str(summary["id"])
                if article_id in seen:
                    continue
                seen.add(article_id)

                record = summary
                if not isinstance(record.get("body"), str) or not record.get("html_url"):
                    detail = self._fetch_detail(article_id)
                    if detail is None:
                        continue
                    record = {**summary, **detail}

                if record.get("draft") is True or record.get("published") is False:
                    continue
                record_locale = str(record.get("locale") or self.locale)
                if record_locale.lower() != self.locale.lower():
                    continue
                title = str(record.get("title") or "").strip()
                body = str(record.get("body") or "")
                canonical_url = str(record.get("html_url") or "").strip()
                if not title or not body.strip() or not canonical_url:
                    logger.warning("Article %s is missing required public content", article_id)
                    continue

                yield Article(
                    id=article_id,
                    title=title,
                    slug=str(record.get("slug") or ""),
                    body=body,
                    url=canonical_url,
                    locale=record_locale,
                    updated_at=str(record.get("updated_at") or ""),
                )
                yielded += 1
                if self.article_limit is not None and yielded >= self.article_limit:
                    return

            next_url = payload.get("next_page")
            if next_url is not None and not isinstance(next_url, str):
                raise RuntimeError("Zendesk returned an invalid next_page value.")
            url = next_url or ""
            params = None

    def close(self) -> None:
        self.session.close()
