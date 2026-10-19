
import re
import unicodedata


def slugify(title, article_id):
    normalized = unicodedata.normalize("NFKD", title)
    normalized = normalized.encode(
        "ascii", "ignore"
    ).decode("ascii")

    slug = normalized.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-") or "article"

    # ID prevents filename collisions between similar titles.
    return f"{slug}-{article_id}.md"
