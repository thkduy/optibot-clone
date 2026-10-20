from src.scraper.zendesk_client import ZendeskClient


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.headers = {}
        self.calls = []

    def mount(self, *args):
        pass

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        return Response(next(self.responses))

    def close(self):
        pass


def article(article_id, **overrides):
    return {
        "id": article_id,
        "title": f"Title {article_id}",
        "slug": f"title-{article_id}",
        "body": "<p>Public body</p>",
        "html_url": f"https://support.optisigns.com/articles/{article_id}",
        "locale": "en-us",
        "updated_at": "2026-01-01T00:00:00Z",
        **overrides,
    }


def test_paginates_deduplicates_and_excludes_unpublished():
    session = Session([
        {
            "articles": [article(1), article(2, draft=True)],
            "next_page": "https://support.optisigns.com/api/v2/page/2",
        },
        {"articles": [article(1), article(3, locale="fr") , article(4)]},
    ])
    client = ZendeskClient(
        "https://support.optisigns.com", "en-us", session=session
    )
    results = list(client.list_articles())
    assert [item.id for item in results] == ["1", "4"]
    assert results[0].url == "https://support.optisigns.com/articles/1"
    assert len(session.calls) == 2
    assert session.calls[0][1] == {"per_page": 100}
    assert session.calls[1][1] is None


def test_configured_limit_stops_after_eligible_articles():
    session = Session([
        {"articles": [article(1), article(2), article(3)]},
    ])
    client = ZendeskClient(
        "https://support.optisigns.com", "en-us",
        article_limit=2, session=session,
    )
    assert [item.id for item in client.list_articles()] == ["1", "2"]
