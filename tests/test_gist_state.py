import json
from pathlib import Path

from config import Settings
from src.sync.state_store import StateStore


class Response:
    def __init__(self, payload):
        self.payload = payload
        self.content = b""

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeGithubSession:
    def __init__(self):
        self.headers = {}
        self.files = {}
        self.patch_calls = []

    def mount(self, *args):
        pass

    def get(self, url, timeout):
        return Response({
            "files": {
                name: {"content": content, "truncated": False}
                for name, content in self.files.items()
            }
        })

    def patch(self, url, *, json, timeout):
        self.patch_calls.append(json)
        for name, spec in json["files"].items():
            self.files[name] = spec["content"]
        return Response({
            "files": {
                name: {"filename": name} for name in json["files"]
            }
        })


def test_gist_backend_persists_manifest_and_sanitized_last_run(tmp_path, monkeypatch):
    session = FakeGithubSession()
    monkeypatch.setattr("src.sync.state_store.requests.Session", lambda: session)
    settings = Settings(
        gemini_api_key="not-used",
        gemini_file_search_store="stores/test",
        zendesk_base_url="https://support.optisigns.com",
        article_locale="en-us",
        min_articles=30,
        article_limit=None,
        output_dir=tmp_path / "output",
        state_path=tmp_path / "manifest.json",
        last_run_path=tmp_path / "data" / "last-run.json",
        state_backend="github_gist",
        log_level="INFO",
        github_token="test-token",
        github_gist_id="gist-id",
        github_gist_manifest_file="manifest.json",
        github_gist_last_run_file="last-run.json",
        poll_seconds=5,
        max_wait_seconds=1200,
    )
    state = StateStore(settings)
    assert state.load() == {"version": 1, "articles": {}}
    assert session.headers["Authorization"] == "Bearer test-token"

    state.save_entry(
        "42", slug="stable-slug", url="https://support.optisigns.com/a",
        digest="hash", remote_file_id="fileSearchStores/store/documents/42",
        status="added",
    )
    state.save_run_summary({
        "added": 1,
        "failed": 0,
        "uploaded_files": 1,
        "total_estimated_chunks": None,
    })

    manifest = json.loads(session.files["manifest.json"])
    assert manifest["articles"]["42"]["remote_file_id"].endswith("/42")
    assert "last-run.json" in session.files
    report = json.loads(session.files["last-run.json"])
    assert report["total_estimated_chunks"] is None
    assert len(session.patch_calls) == 2
