"""Atomic manifest storage, content hashing, and change classification."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Iterator

from filelock import FileLock, Timeout
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import Settings


logger = logging.getLogger(__name__)
VALID_STATUSES = {"added", "updated", "skipped"}


def content_hash(markdown: str) -> str:
    """Hash normalized UTF-8 Markdown, making line endings and trailing spaces stable."""
    normalized = "\n".join(line.rstrip() for line in markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    normalized = normalized.strip() + "\n"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def classify_article(article_id: str, digest: str, manifest: dict) -> str:
    previous = manifest.get("articles", {}).get(str(article_id))
    if not isinstance(previous, dict) or not previous.get("remote_file_id"):
        return "added"
    return "skipped" if previous.get("content_hash") == digest else "updated"


class StateStore:
    """Local JSON or GitHub Gist state storage."""

    def __init__(self, settings: Settings) -> None:
        self.path = settings.state_path
        self.last_run_path = settings.last_run_path
        self.state_backend = settings.state_backend
        self._github = None
        self._manifest: dict | None = None
        if self.state_backend == "github_gist":
            self._github_token = settings.github_token
            self._gist_id = settings.github_gist_id
            self._gist_manifest_file = settings.github_gist_manifest_file
            self._gist_last_run_file = settings.github_gist_last_run_file
            self._github = requests.Session()
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
            self._github.mount("https://", HTTPAdapter(max_retries=retry))
            self._github.headers.update({
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {settings.github_token}",
                "X-GitHub-Api-Version": "2022-11-28",
            })

    def _gist_url(self) -> str:
        self._github.headers["Authorization"] = "Bearer " + self._github_token
        return f"https://api.github.com/gists/{self._gist_id}"

    def _gist_file(self, filename: str) -> bytes | None:
        response = self._github.get(self._gist_url(), timeout=(10, 30))
        response.raise_for_status()
        try:
            gist = response.json()
            item = gist["files"].get(filename)
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError("GitHub returned an invalid Gist response.") from exc
        if item is None:
            return None
        if not isinstance(item, dict):
            raise RuntimeError(f"GitHub returned invalid Gist file metadata for {filename}.")
        content = item.get("content")
        if item.get("truncated"):
            raw_url = item.get("raw_url")
            if not raw_url:
                raise RuntimeError(f"GitHub truncated Gist file {filename} without a raw URL.")
            raw_response = self._github.get(raw_url, timeout=(10, 30))
            raw_response.raise_for_status()
            return raw_response.content
        if not isinstance(content, str):
            raise RuntimeError(f"GitHub Gist file {filename} has no readable content.")
        return content.encode("utf-8")

    def _put_gist_file(self, filename: str, content: str) -> None:
        response = self._github.patch(
            self._gist_url(),
            json={"files": {filename: {"content": content}}},
            timeout=(10, 30),
        )
        response.raise_for_status()
        try:
            files = response.json().get("files")
        except (ValueError, AttributeError) as exc:
            raise RuntimeError("GitHub did not confirm the Gist file update.") from exc
        if not isinstance(files, dict) or filename not in files:
            raise RuntimeError(f"GitHub did not confirm updating Gist file {filename}.")

    def load(self) -> dict:
        if self._manifest is not None:
            return self._manifest
        try:
            if self.state_backend == "github_gist":
                raw = self._gist_file(self._gist_manifest_file)
                if raw is None:
                    self._manifest = {"version": 1, "articles": {}}
                    return self._manifest
            elif self.path.exists():
                raw = self.path.read_bytes()
            else:
                self._manifest = {"version": 1, "articles": {}}
                return self._manifest
        except FileNotFoundError:
            self._manifest = {"version": 1, "articles": {}}
            return self._manifest
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or not isinstance(data.get("articles"), dict):
                raise ValueError("manifest root or articles map has invalid shape")
            self._manifest = data
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
            logger.warning("Malformed synchronization manifest; starting empty: %s", exc)
            self._manifest = {"version": 1, "articles": {}}
        return self._manifest

    def save_run_summary(self, summary: dict) -> None:
        """Write a sanitized run report locally or to the configured Gist."""
        payload = json.dumps(summary, indent=2, sort_keys=True) + "\n"
        if self.state_backend == "github_gist":
            self._put_gist_file(self._gist_last_run_file, payload)
            return
        report_path = self.last_run_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(payload, encoding="utf-8", newline="\n")

    def save_entry(
        self, article_id: str, *, slug: str, url: str, digest: str,
        remote_file_id: str, status: str,
    ) -> None:
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid synchronization status: {status}")
        current = self.load()
        manifest = {
            "version": 1,
            "articles": dict(current["articles"]),
        }
        manifest["articles"][str(article_id)] = {
            "article_id": str(article_id),
            "slug": slug,
            "url": url,
            "content_hash": digest,
            "remote_file_id": remote_file_id,
            "last_synced_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
        }
        payload = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if self.state_backend == "github_gist":
            self._put_gist_file(self._gist_manifest_file, payload)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temp_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as temp:
                    temp.write(payload)
                    temp.flush()
                    os.fsync(temp.fileno())
                os.replace(temp_name, self.path)
            except Exception:
                try:
                    os.unlink(temp_name)
                except FileNotFoundError:
                    pass
                raise
        self._manifest = manifest

    @contextmanager
    def lock(self, timeout: float = 1.0) -> Iterator[None]:
        """Prevent overlapping local processes; Gist mode requires one job writer."""
        lock = FileLock(str(self.path) + ".lock")
        try:
            lock.acquire(timeout=timeout)
        except Timeout as exc:
            raise RuntimeError("Another synchronization job holds the state lock.") from exc
        try:
            yield
        finally:
            lock.release()
