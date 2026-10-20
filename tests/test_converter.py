from pathlib import Path

from src.converter.markdown_converter import (
    html_to_markdown,
    safe_filename,
    write_article,
)


def test_preserves_headings_paragraphs_and_lists():
    markdown = html_to_markdown(
        "Guide",
        "<h2>Setup</h2><p>Start here.</p><ol><li>One</li><li>Two</li></ol>",
        "https://support.optisigns.com/a",
    )
    assert "## Setup" in markdown
    assert "Start here." in markdown
    assert "1. One" in markdown
    assert "2. Two" in markdown


def test_preserves_code_and_resolves_relative_links():
    markdown = html_to_markdown(
        "Guide",
        '<p>Use <code>run()</code>.</p><pre><code class="language-python">'
        "print('ok')\n</code></pre><a href='/hc/en-us/articles/1'>Article</a>",
        "https://support.optisigns.com/hc/en-us/articles/1",
    )
    assert "`run()`" in markdown
    assert "```python" in markdown
    assert "print('ok')" in markdown
    assert "(https://support.optisigns.com/hc/en-us/articles/1)" in markdown


def test_removes_navigation_and_keeps_article_content():
    markdown = html_to_markdown(
        "Guide",
        "<nav>Menu</nav><div class='cookie-banner'>Cookies</div>"
        "<article><p>Useful troubleshooting step.</p></article>",
        "https://support.optisigns.com/a",
    )
    assert "Useful troubleshooting step." in markdown
    assert "Menu" not in markdown
    assert "Cookies" not in markdown


def test_empty_and_malformed_html_are_safe():
    assert "Article URL: https://support.optisigns.com/a" in html_to_markdown(
        "Empty", "", "https://support.optisigns.com/a"
    )
    assert "partial content" in html_to_markdown(
        "Broken", "<p>partial content<div>", "https://support.optisigns.com/a"
    )


def test_filename_is_safe_and_deterministic(tmp_path: Path):
    name = safe_filename("../Crème brûlée / ?!", "42")
    assert name == safe_filename("../Crème brûlée / ?!", "42")
    assert "/" not in name and "\\" not in name and name.endswith("-42.md")
    path = write_article(
        tmp_path, "42", "how-to-install", "# Test\n", "Install guide"
    )
    assert path.name == "how-to-install-42.md"
    assert path.exists()


def test_empty_article_slug_falls_back_to_title(tmp_path: Path):
    path = write_article(
        tmp_path, "43", "", "# Test\n", "Add a YouTube Video!"
    )
    assert path.name == "add-a-youtube-video-43.md"
