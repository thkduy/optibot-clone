
from pathlib import Path

from zendesk_client import ZendeskClient
from converter import html_to_markdown
from utils import slugify

OUTPUT_DIR = Path("output")
MIN_ARTICLES = 30


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    client = ZendeskClient()
    exported = 0
    failed = 0
    seen_ids = set()

    try:
        for summary in client.list_articles():
            article_id = summary.get("id")

            if not article_id or article_id in seen_ids:
                continue

            seen_ids.add(article_id)

            try:
                article = client.get_article(article_id)

                title = article.get("title") or "Untitled"
                body = article.get("body") or ""

                if not body.strip():
                    print(f"Skipped empty article: {article_id}")
                    failed += 1
                    continue

                markdown = html_to_markdown(title, body)
                filename = slugify(title, article_id)

                (OUTPUT_DIR / filename).write_text(
                    markdown,
                    encoding="utf-8",
                )

                exported += 1
                print(f"[{exported}] {filename}")

                if exported >= MIN_ARTICLES:
                    break

            except Exception as exc:
                failed += 1
                print(f"Failed article {article_id}: {exc}")

    finally:
        client.close()

    print(f"\nExported: {exported}")
    print(f"Failed or skipped: {failed}")
    print(f"Output directory: {OUTPUT_DIR.resolve()}")

    if exported < MIN_ARTICLES:
        raise RuntimeError(
            f"Requirement not met: only {exported} articles "
            f"exported; need at least {MIN_ARTICLES}."
        )


if __name__ == "__main__":
    main()
