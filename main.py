"""One-shot scheduled knowledge synchronization entry point."""

import logging
import sys
import time
from pathlib import Path
from typing import Any

from config import load_settings
from src.converter.markdown_converter import html_to_markdown, write_article
from src.scraper.zendesk_client import Article, ZendeskClient
from src.sync.gemini_uploader import GeminiUploader
from src.sync.state_store import StateStore, classify_article, content_hash
from src.utils.logger import configure_logging


logger = logging.getLogger(__name__)


def fetch_articles(settings: Any) -> list[Article]:
    client = ZendeskClient(
        settings.zendesk_base_url,
        settings.article_locale,
        settings.article_limit,
    )
    try:
        articles = list(client.list_articles())
    finally:
        client.close()
    unique = {article.id: article for article in articles}
    return list(unique.values())


def convert_articles(
    articles: list[Article], output_dir: Path
) -> list[tuple[Article, Path, str]]:
    converted = []
    for article in articles:
        markdown = html_to_markdown(article.title, article.body, article.url)
        path = write_article(
            output_dir, article.id, article.slug, markdown, article.title
        )
        converted.append((article, path, content_hash(markdown)))
    return converted


def run_sync() -> int:
    started = time.monotonic()
    counts = {
        "articles_fetched": 0,
        "markdown_generated": 0,
        "added": 0,
        "updated": 0,
        "skipped": 0,
        "files_uploaded": 0,
        "files_processed": 0,
        "failed_files": 0,
    }
    success = False
    state = None
    try:
        settings = load_settings()
        configure_logging(settings.log_level)
        logger.info("Sync started")
        state = StateStore(settings)
        with state.lock():
            articles = fetch_articles(settings)
            counts["articles_fetched"] = len(articles)
            logger.info("Articles fetched: %d", len(articles))
            if len(articles) < settings.min_articles:
                raise RuntimeError(
                    f"Only {len(articles)} eligible public articles found; "
                    f"minimum is {settings.min_articles}."
                )

            converted = convert_articles(articles, settings.output_dir)
            counts["markdown_generated"] = len(converted)
            logger.info("Markdown generated: %d", len(converted))
            manifest = state.load()
            uploader = GeminiUploader(
                settings.gemini_api_key,
                settings.gemini_file_search_store,
                settings.poll_seconds,
                settings.max_wait_seconds,
            )

            for article, path, digest in converted:
                status = classify_article(article.id, digest, manifest)
                counts[status] += 1
                if status == "skipped":
                    previous = manifest["articles"][article.id]
                    if (
                        previous.get("slug") != article.slug
                        or previous.get("url") != article.url
                    ):
                        state.save_entry(
                            article.id,
                            slug=article.slug,
                            url=article.url,
                            digest=digest,
                            remote_file_id=previous["remote_file_id"],
                            status="skipped",
                        )
                        manifest = state.load()
                    continue
                try:
                    _, remote_id, uploaded = uploader.sync_file(
                        article.id, digest, path
                    )
                    counts["files_uploaded"] += int(uploaded)
                    counts["files_processed"] += 1
                    state.save_entry(
                        article.id,
                        slug=article.slug,
                        url=article.url,
                        digest=digest,
                        remote_file_id=remote_id,
                        status=status,
                    )
                    manifest = state.load()
                except Exception:
                    counts["failed_files"] += 1
                    logger.exception("Failed to synchronize article %s", article.id)

        if counts["failed_files"]:
            raise RuntimeError(
                f"{counts['failed_files']} article synchronization operation(s) failed."
            )
        success = True
        return 0
    except Exception:
        logger.exception("Sync failed")
        return 1
    finally:
        duration = time.monotonic() - started
        logger.info("Articles fetched: %d", counts["articles_fetched"])
        logger.info("Markdown files generated: %d", counts["markdown_generated"])
        logger.info("Added: %d", counts["added"])
        logger.info("Updated: %d", counts["updated"])
        logger.info("Skipped: %d", counts["skipped"])
        logger.info("Files uploaded: %d", counts["files_uploaded"])
        logger.info("Files processed successfully: %d", counts["files_processed"])
        logger.info("Failed files: %d", counts["failed_files"])
        logger.info("Processing duration: %.2f seconds", duration)
        logger.info("Overall result: %s", "success" if success else "failure")
        if state is not None:
            try:
                state.save_run_summary({
                    **counts,
                    "failed": counts["failed_files"],
                    "uploaded_files": counts["files_uploaded"],
                    "total_estimated_chunks": None,
                    "duration_seconds": round(duration, 2),
                    "success": success,
                })
            except Exception:
                logger.exception("Could not write sanitized last-run report")


def main() -> int:
    return run_sync()


if __name__ == "__main__":
    sys.exit(main())
