import json
from pathlib import Path
from types import SimpleNamespace

import main as pipeline
from config import Settings
from src.scraper.zendesk_client import Article
from src.converter.markdown_converter import html_to_markdown
from src.sync.gemini_uploader import GeminiUploader
from src.sync.state_store import StateStore


class FakeUploader:
    uploads = []

    def __init__(self, *args):
        pass

    def sync_file(self, article_id, digest, file_path):
        self.uploads.append((article_id, digest))
        return "added", f"documents/{article_id}", True


def make_settings(tmp_path):
    return Settings(
        gemini_api_key="test",
        gemini_file_search_store="stores/test",
        zendesk_base_url="https://support.optisigns.com",
        article_locale="en-us",
        min_articles=1,
        article_limit=None,
        output_dir=tmp_path / "output",
        state_path=tmp_path / "state" / "manifest.json",
        last_run_path=tmp_path / "data" / "last-run.json",
        state_backend="local",
        log_level="CRITICAL",
        github_token=None,
        github_gist_id=None,
        github_gist_manifest_file="manifest.json",
        github_gist_last_run_file="last-run.json",
        poll_seconds=0,
        max_wait_seconds=2,
    )


def test_first_run_uploads_and_second_unchanged_run_skips(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    articles = [Article("1", "Title", "title", "<p>Body</p>",
                        "https://support.optisigns.com/a", "en-us", "")]
    monkeypatch.setattr(pipeline, "load_settings", lambda: settings)
    monkeypatch.setattr(pipeline, "fetch_articles", lambda _: articles)
    monkeypatch.setattr(pipeline, "GeminiUploader", FakeUploader)
    FakeUploader.uploads = []

    assert pipeline.run_sync() == 0
    assert len(FakeUploader.uploads) == 1
    assert pipeline.run_sync() == 0
    assert len(FakeUploader.uploads) == 1
    assert StateStore(settings).load()["articles"]["1"]["remote_file_id"] == "documents/1"
    report = json.loads(settings.last_run_path.read_text(encoding="utf-8"))
    assert report["skipped"] == 1
    assert report["total_estimated_chunks"] is None


def test_partial_upload_failure_returns_nonzero_and_keeps_old_state(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    state = StateStore(settings)
    state.save_entry(
        "1", slug="title", url="https://support.optisigns.com/a",
        digest="old", remote_file_id="documents/old", status="added",
    )

    class FailedUploader:
        def __init__(self, *args):
            pass

        def sync_file(self, *args):
            raise RuntimeError("processing failed")

    monkeypatch.setattr(pipeline, "load_settings", lambda: settings)
    monkeypatch.setattr(pipeline, "fetch_articles", lambda _: [
        Article("1", "Title", "title", "<p>Changed</p>",
                "https://support.optisigns.com/a", "en-us", "")
    ])
    monkeypatch.setattr(pipeline, "GeminiUploader", FailedUploader)
    assert pipeline.run_sync() == 1
    assert state.load()["articles"]["1"]["content_hash"] == "old"


def test_changed_article_replaces_previous_manifest_reference(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    state = StateStore(settings)
    state.save_entry(
        "1", slug="title", url="https://support.optisigns.com/a",
        digest="old", remote_file_id="documents/old", status="added",
    )
    monkeypatch.setattr(pipeline, "load_settings", lambda: settings)
    monkeypatch.setattr(pipeline, "fetch_articles", lambda _: [
        Article("1", "Title", "new-title", "<p>Changed</p>",
                "https://support.optisigns.com/a", "en-us", "")
    ])
    FakeUploader.uploads = []
    monkeypatch.setattr(pipeline, "GeminiUploader", FakeUploader)
    assert pipeline.run_sync() == 0
    entry = StateStore(settings).load()["articles"]["1"]
    assert entry["remote_file_id"] == "documents/1"
    assert entry["content_hash"] != "old"
    assert len(FakeUploader.uploads) == 1


def test_slug_change_updates_same_article_record_without_upload(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    state = StateStore(settings)
    article = Article(
        "1", "Title", "new-slug", "<p>Body</p>",
        "https://support.optisigns.com/a", "en-us", "",
    )
    digest = pipeline.content_hash(
        html_to_markdown(article.title, article.body, article.url)
    )
    state.save_entry(
        "1", slug="old-slug", url=article.url, digest=digest,
        remote_file_id="documents/old", status="added",
    )
    monkeypatch.setattr(pipeline, "load_settings", lambda: settings)
    monkeypatch.setattr(pipeline, "fetch_articles", lambda _: [article])
    FakeUploader.uploads = []
    monkeypatch.setattr(pipeline, "GeminiUploader", FakeUploader)

    assert pipeline.run_sync() == 0
    saved = StateStore(settings).load()["articles"]
    assert list(saved) == ["1"]
    assert saved["1"]["slug"] == "new-slug"
    assert FakeUploader.uploads == []


class FakeDocuments:
    def __init__(self, docs):
        self.docs = docs
        self.deleted = []

    def list(self, parent):
        return list(self.docs)

    def delete(self, name, config):
        self.deleted.append(name)
        self.docs = [doc for doc in self.docs if doc.name != name]


class FakeSearchStores:
    def __init__(self, documents, fail=False):
        self.documents = documents
        self.fail = fail

    def upload_to_file_search_store(self, file, file_search_store_name, config):
        self.documents.docs.append(SimpleNamespace(
            name="documents/new",
            display_name=config["display_name"],
            state="PROCESSING",
        ))
        return SimpleNamespace(done=False)


class FakeClient:
    def __init__(self, docs, fail=False):
        self.file_search_stores = FakeSearchStores(docs, fail)
        self.file_search_stores.documents = docs
        self.operations = SimpleNamespace(get=self.get_operation)
        self.fail = fail

    def get_operation(self, operation):
        if self.fail:
            return SimpleNamespace(done=True, error="processing failed")
        for doc in self.file_search_stores.documents.docs:
            if doc.name == "documents/new":
                doc.state = "ACTIVE"
        return SimpleNamespace(done=True, error=None)


def test_replacement_deletes_old_only_after_new_document_is_active(tmp_path, monkeypatch):
    old = SimpleNamespace(
        name="documents/old",
        display_name="optibot-article-1-oldhash",
        state="ACTIVE",
    )
    documents = FakeDocuments([old])
    client = FakeClient(documents)
    monkeypatch.setattr("src.sync.gemini_uploader.time.sleep", lambda _: None)
    uploader = GeminiUploader("unused", "stores/test", 1, 3, client=client)
    status, remote_id, uploaded = uploader.sync_file("1", "newhash", tmp_path / "a.md")
    assert status == "updated"
    assert remote_id == "documents/new"
    assert uploaded is True
    assert documents.deleted == ["documents/old"]


def test_failed_processing_keeps_old_document(tmp_path, monkeypatch):
    old = SimpleNamespace(
        name="documents/old",
        display_name="optibot-article-1-oldhash",
        state="ACTIVE",
    )
    documents = FakeDocuments([old])
    client = FakeClient(documents, fail=True)
    monkeypatch.setattr("src.sync.gemini_uploader.time.sleep", lambda _: None)
    uploader = GeminiUploader("unused", "stores/test", 1, 3, client=client)
    try:
        uploader.sync_file("1", "newhash", tmp_path / "a.md")
    except RuntimeError as exc:
        assert "processing failed" in str(exc)
    else:
        raise AssertionError("Expected processing failure")
    assert documents.deleted == []


def test_ambiguous_upload_is_reconciled_without_duplicate(tmp_path, monkeypatch):
    from httpx import TimeoutException

    class AcceptedThenTimedOut(FakeSearchStores):
        attempts = 0

        def upload_to_file_search_store(self, file, file_search_store_name, config):
            self.attempts += 1
            self.documents.docs.append(SimpleNamespace(
                name="documents/new",
                display_name=config["display_name"],
                state="ACTIVE",
            ))
            raise TimeoutException("response timed out after upload")

    old = SimpleNamespace(
        name="documents/old",
        display_name="optibot-article-1-oldhash",
        state="ACTIVE",
    )
    documents = FakeDocuments([old])
    client = FakeClient(documents)
    search = AcceptedThenTimedOut(documents)
    client.file_search_stores = search
    client.operations = SimpleNamespace(get=client.get_operation)
    monkeypatch.setattr("src.sync.gemini_uploader.time.sleep", lambda _: None)
    uploader = GeminiUploader("unused", "stores/test", 1, 3, client=client)

    status, remote_id, uploaded = uploader.sync_file(
        "1", "newhash", tmp_path / "a.md"
    )
    assert status == "updated"
    assert remote_id == "documents/new"
    assert uploaded is True
    assert search.attempts == 1
    assert documents.deleted == ["documents/old"]
