
import re
from bs4 import BeautifulSoup
from markdownify import MarkdownConverter


class ArticleMarkdownConverter(MarkdownConverter):
    """Convert article HTML into clean Markdown."""

    def convert_pre(self, el, text, parent_tags):
        code = el.get_text()
        fence = "```"

        # Avoid breaking the fence if the code contains backticks.
        longest = max(
            (len(match.group()) for match in re.finditer(r"`+", code)),
            default=0,
        )
        fence = "`" * max(3, longest + 1)

        language = ""
        code_el = el.find("code")
        if code_el:
            classes = code_el.get("class", [])
            for class_name in classes:
                if class_name.startswith("language-"):
                    language = class_name.removeprefix("language-")
                    break

        return f"\n\n{fence}{language}\n{code.rstrip()}\n{fence}\n\n"


def html_to_markdown(title: str, html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")

    # Remove elements that should not appear in article content.
    for element in soup.select(
        "script, style, nav, aside, "
        "[role='navigation'], "
        ".advertisement, .ads, .promo"
    ):
        element.decompose()

    # Prefer the main article content when a page wrapper is present.
    article = (
        soup.select_one("article")
        or soup.select_one(".article-body")
        or soup.select_one(".article-content")
        or soup.body
        or soup
    )

    markdown = ArticleMarkdownConverter(
        heading_style="ATX",
        bullets="-",
    ).convert(str(article))

    # Normalize excessive blank lines without modifying code contents.
    markdown = markdown.strip()

    return f"# {title.strip()}\n\n{markdown}\n"
