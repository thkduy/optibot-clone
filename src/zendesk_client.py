
import requests

BASE_URL = "https://support.optisigns.com/api/v2/help_center"
LOCALE = "en-us"


class ZendeskClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "OptiSignsMarkdownExporter/1.0",
        })

    def list_articles(self):
        url = f"{BASE_URL}/{LOCALE}/articles.json"
        params = {"per_page": 100}

        while url:
            response = self.session.get(
                url, params=params, timeout=30
            )
            response.raise_for_status()
            data = response.json()

            yield from data.get("articles", [])

            # Zendesk returns the next page URL when available.
            url = data.get("next_page")
            params = None

    def get_article(self, article_id):
        url = (
            f"{BASE_URL}/{LOCALE}/articles/"
            f"{article_id}.json"
        )
        response = self.session.get(url, timeout=30)
        response.raise_for_status()

        return response.json()["article"]

    def close(self):
        self.session.close()
