"""Gemini File Search Store upload, processing verification, and replacement."""

import logging
import time
from pathlib import Path
from typing import Any

from google import genai
from google.genai import errors
from httpx import NetworkError, TimeoutException
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)


logger = logging.getLogger(__name__)
DOCUMENT_PREFIX = "optibot-article-"


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (errors.ServerError, NetworkError, TimeoutException)):
        return True
    return (
        isinstance(exc, errors.APIError)
        and (exc.code in (408, 429) or exc.code >= 500)
    )


def _value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class GeminiUploader:
    def __init__(
        self, api_key: str, store_name: str, poll_seconds: int = 5,
        max_wait_seconds: int = 1200, client: Any | None = None,
    ) -> None:
        self.client = client or genai.Client(api_key=api_key)
        self.store_name = store_name
        self.poll_seconds = poll_seconds
        self.max_wait_seconds = max_wait_seconds

    @retry(
        retry=retry_if_exception(_is_transient),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def list_documents(self) -> list[Any]:
        """List indexed documents through the SDK's pageable collection."""
        documents = self.client.file_search_stores.documents.list(
            parent=self.store_name
        )
        return list(documents)

    @staticmethod
    def _display_name(article_id: str, digest: str) -> str:
        return f"{DOCUMENT_PREFIX}{article_id}-{digest}"

    def _wait_for_operation(self, operation: Any) -> Any:
        started = time.monotonic()
        while not _value(operation, "done", False):
            if time.monotonic() - started > self.max_wait_seconds:
                raise TimeoutError("Timed out waiting for Gemini File Search indexing.")
            time.sleep(self.poll_seconds)
            operation = self._get_operation(operation)
        error = _value(operation, "error")
        if error:
            raise RuntimeError(f"Gemini File Search processing failed: {error}")
        return operation

    def _active_match(self, documents: list[Any], display_name: str) -> Any | None:
        matches = self._matching_documents(documents, display_name)
        for document in matches:
            state = str(_value(document, "state", "")).upper()
            if state.endswith("ACTIVE"):
                return document
            if state.endswith("FAILED"):
                self._delete_document(document)
                raise RuntimeError(
                    f"Gemini reports processing failure for {display_name}"
                )
        return None

    @staticmethod
    def _matching_documents(documents: list[Any], display_name: str) -> list[Any]:
        return [
            doc for doc in documents
            if _value(doc, "display_name", "") == display_name
        ]

    def _wait_for_document(self, display_name: str, documents: list[Any]) -> Any | None:
        started = time.monotonic()
        while True:
            match = self._active_match(documents, display_name)
            if match is not None:
                return match
            pending = self._matching_documents(documents, display_name)
            if not pending:
                return None
            if time.monotonic() - started > self.max_wait_seconds:
                raise TimeoutError(
                    f"Timed out waiting for Gemini document {display_name}."
                )
            if any(not _value(doc, "state") for doc in pending):
                raise RuntimeError(
                    f"Gemini returned a document with no processing state: {display_name}"
                )
            time.sleep(self.poll_seconds)
            documents = self.list_documents()

    @retry(
        retry=retry_if_exception(_is_transient),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _get_operation(self, operation: Any) -> Any:
        return self.client.operations.get(operation)

    @retry(
        retry=retry_if_exception(_is_transient),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _delete_document(self, document: Any) -> None:
        name = _value(document, "name")
        if not name:
            raise RuntimeError("Gemini returned an obsolete document without a name.")
        self.client.file_search_stores.documents.delete(
            name=name, config={"force": True}
        )

    def sync_file(
        self, article_id: str, digest: str, file_path: Path,
    ) -> tuple[str, str, bool]:
        """Index or reuse a content version, then remove prior versions."""
        display_name = self._display_name(article_id, digest)
        documents = self.list_documents()
        match = self._wait_for_document(display_name, documents)
        article_prefix = f"{DOCUMENT_PREFIX}{article_id}-"
        previous = [
            doc for doc in documents
            if str(_value(doc, "display_name", "")).startswith(article_prefix)
            and _value(doc, "name") != _value(match, "name")
        ]

        uploaded = match is None
        if uploaded:
            attempt = 0
            while True:
                try:
                    operation = self.client.file_search_stores.upload_to_file_search_store(
                        file=str(file_path),
                        file_search_store_name=self.store_name,
                        config={
                            "display_name": display_name,
                            "chunking_config": {
                                "white_space_config": {
                                    "max_tokens_per_chunk": 200,
                                    "max_overlap_tokens": 20,
                                }
                            },
                        },
                    )
                except Exception as upload_error:
                    # Reconcile by deterministic name before retrying an ambiguous
                    # network failure that may already have created the document.
                    try:
                        refreshed = self.list_documents()
                        match = self._wait_for_document(display_name, refreshed)
                    except Exception as reconcile_error:
                        raise upload_error from reconcile_error
                    if match is None and _is_transient(upload_error):
                        for _ in range(3):
                            time.sleep(min(self.poll_seconds, 5))
                            refreshed = self.list_documents()
                            match = self._wait_for_document(display_name, refreshed)
                            if match is not None:
                                break
                    if match is not None:
                        break
                    if not _is_transient(upload_error) or attempt >= 2:
                        raise
                    time.sleep(min(2 ** attempt, 8))
                    attempt += 1
                    continue

                self._wait_for_operation(operation)
                refreshed = self.list_documents()
                match = self._wait_for_document(display_name, refreshed)
                break
            if match is None:
                raise RuntimeError(
                    f"Gemini upload completed without an ACTIVE document ({display_name})."
                )
            # Reload old versions after processing to avoid deleting the new one.
            previous = [
                doc for doc in refreshed
                if str(_value(doc, "display_name", "")).startswith(article_prefix)
                and _value(doc, "name") != _value(match, "name")
            ]

        file_id = str(_value(match, "name") or "")
        if not file_id:
            raise RuntimeError(f"Gemini document {display_name} has no remote name.")
        for obsolete in previous:
            self._delete_document(obsolete)
            logger.info("Removed obsolete Gemini document %s", _value(obsolete, "name"))
        return ("updated" if previous else "added", file_id, uploaded)
