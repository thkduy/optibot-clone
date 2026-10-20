"""Environment-backed configuration for the knowledge sync job."""

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    gemini_file_search_store: str
    zendesk_base_url: str
    article_locale: str
    min_articles: int
    article_limit: int | None
    output_dir: Path
    state_path: Path
    last_run_path: Path
    state_backend: str
    log_level: str
    github_token: str | None
    github_gist_id: str | None
    github_gist_manifest_file: str
    github_gist_last_run_file: str
    poll_seconds: int
    max_wait_seconds: int


def load_settings() -> Settings:
    """Load settings and reject missing required values before starting work."""
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    store_name = os.getenv("GEMINI_FILE_SEARCH_STORE", "").strip()
    missing = [
        name for name, value in (
            ("GEMINI_API_KEY", api_key),
            ("GEMINI_FILE_SEARCH_STORE", store_name),
        ) if not value
    ]
    if missing:
        raise ValueError(
            "Missing required configuration: " + ", ".join(missing)
            + ". See .env.sample."
        )

    try:
        minimum = int(os.getenv("MIN_ARTICLES", "30"))
        raw_limit = os.getenv("ARTICLE_LIMIT", "").strip()
        limit = int(raw_limit) if raw_limit else None
        poll_seconds = int(os.getenv("POLL_SECONDS", "5"))
        max_wait_seconds = int(os.getenv("MAX_WAIT_SECONDS", "1200"))
    except ValueError as exc:
        raise ValueError("Article and polling limits must be integers.") from exc
    if minimum < 0 or (limit is not None and limit <= 0):
        raise ValueError("MIN_ARTICLES must be nonnegative and ARTICLE_LIMIT positive.")
    if poll_seconds <= 0 or max_wait_seconds <= 0:
        raise ValueError("POLL_SECONDS and MAX_WAIT_SECONDS must be positive.")

    base_url = os.getenv(
        "ZENDESK_BASE_URL", "https://support.optisigns.com"
    ).strip().rstrip("/")
    if not base_url.startswith(("https://", "http://")):
        raise ValueError("ZENDESK_BASE_URL must be an absolute HTTP(S) URL.")

    state_backend = os.getenv("STATE_BACKEND", "github_gist").strip().lower()
    if state_backend not in {"local", "github_gist"}:
        raise ValueError("STATE_BACKEND must be local or github_gist.")
    github_token = os.getenv("GITHUB_TOKEN", "").strip() or None
    gist_id = os.getenv("GITHUB_GIST_ID", "").strip() or None
    gist_manifest_file = os.getenv(
        "GITHUB_GIST_MANIFEST_FILE", "manifest.json"
    ).strip()
    gist_last_run_file = os.getenv(
        "GITHUB_GIST_LAST_RUN_FILE", "last-run.json"
    ).strip()
    if state_backend == "github_gist" and (not github_token or not gist_id):
        raise ValueError(
            "GITHUB_TOKEN and GITHUB_GIST_ID are required when "
            "STATE_BACKEND=github_gist."
        )
    if state_backend == "github_gist" and (
        "/" in gist_id or not gist_manifest_file or not gist_last_run_file
        or gist_manifest_file == gist_last_run_file
    ):
        raise ValueError(
            "GITHUB_GIST_ID must be an ID (not a URL), and Gist filenames cannot be empty."
        )

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    if log_level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
        raise ValueError("LOG_LEVEL must be CRITICAL, ERROR, WARNING, INFO, or DEBUG.")
    locale = os.getenv("ARTICLE_LOCALE", "en-us").strip()
    if not locale:
        raise ValueError("ARTICLE_LOCALE cannot be empty.")
    return Settings(
        gemini_api_key=api_key,
        gemini_file_search_store=store_name,
        zendesk_base_url=base_url,
        article_locale=locale,
        min_articles=minimum,
        article_limit=limit,
        output_dir=Path(os.getenv("OUTPUT_DIR", "output/articles")),
        state_path=Path(os.getenv("STATE_PATH", "state/manifest.json")),
        last_run_path=Path(os.getenv("LAST_RUN_PATH", "state/last-run.json")),
        state_backend=state_backend,
        log_level=log_level,
        github_token=github_token,
        github_gist_id=gist_id,
        github_gist_manifest_file=gist_manifest_file,
        github_gist_last_run_file=gist_last_run_file,
        poll_seconds=poll_seconds,
        max_wait_seconds=max_wait_seconds,
    )
