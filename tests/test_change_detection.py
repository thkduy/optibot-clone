from pathlib import Path

from config import Settings
from src.sync.state_store import StateStore, classify_article, content_hash


def settings(path: Path) -> Settings:
    return Settings(
        gemini_api_key="test",
        gemini_file_search_store="stores/test",
        zendesk_base_url="https://support.optisigns.com",
        article_locale="en-us",
        min_articles=0,
        article_limit=None,
        output_dir=path / "output",
        state_path=path / "manifest.json",
        last_run_path=path / "last-run.json",
        state_backend="local",
        log_level="INFO",
        github_token=None,
        github_gist_id=None,
        github_gist_manifest_file="manifest.json",
        github_gist_last_run_file="last-run.json",
        poll_seconds=1,
        max_wait_seconds=2,
    )


def test_classifies_added_updated_skipped_and_ignores_slug_identity():
    digest = content_hash("# Same\n")
    manifest = {"articles": {}}
    assert classify_article("7", digest, manifest) == "added"
    manifest["articles"]["7"] = {
        "slug": "old-title",
        "remote_file_id": "files/1",
        "content_hash": digest,
    }
    assert classify_article("7", digest, manifest) == "skipped"
    assert classify_article("7", content_hash("# Changed\n"), manifest) == "updated"


def test_failed_upload_does_not_advance_state(tmp_path):
    store = StateStore(settings(tmp_path))
    before = store.load()
    assert classify_article("9", content_hash("old"), before) == "added"
    # A failed remote upload never calls save_entry.
    assert store.load() == {"version": 1, "articles": {}}


def test_manifest_write_is_atomic_and_round_trips(tmp_path):
    store = StateStore(settings(tmp_path))
    store.save_entry(
        "9",
        slug="title",
        url="https://support.optisigns.com/a",
        digest="sha256",
        remote_file_id="documents/123",
        status="added",
    )
    entry = store.load()["articles"]["9"]
    assert entry["remote_file_id"] == "documents/123"
    assert entry["last_synced_at"]
    assert not list(tmp_path.glob("*.tmp"))


def test_malformed_manifest_recovers_without_crashing(tmp_path):
    config = settings(tmp_path)
    config.state_path.parent.mkdir(parents=True, exist_ok=True)
    config.state_path.write_text("{not json", encoding="utf-8")
    assert StateStore(config).load() == {"version": 1, "articles": {}}
