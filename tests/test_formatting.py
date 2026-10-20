from bot.formatting import preview, split_markdown, to_html


def test_inline_styles():
    assert to_html("**жирный** и *курсив* и ~~нет~~") == "<b>жирный</b> и <i>курсив</i> и <s>нет</s>"


def test_html_is_escaped_outside_and_inside_code():
    assert to_html("a < b & `x<y>`") == "a &lt; b &amp; <code>x&lt;y&gt;</code>"


def test_code_block_is_not_formatted():
    out = to_html("```python\nprint(**kw)\n```")
    assert out == '<pre><code class="language-python">print(**kw)</code></pre>'


def test_links_headers_bullets_quotes():
    md = "# Заголовок\n- [сайт](https://example.com/?a=1&b=2)\n> цитата"
    assert to_html(md) == (
        "<b>Заголовок</b>\n"
        '• <a href="https://example.com/?a=1&amp;b=2">сайт</a>\n'
        "<blockquote>цитата</blockquote>"
    )


def test_snake_case_is_not_italic():
    assert to_html("my_var_name") == "my_var_name"


def test_split_keeps_fences_balanced():
    md = "```py\n" + "\n".join(f"line {i}" for i in range(400)) + "\n```\nконец"
    chunks = split_markdown(md, limit=500)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.count("```") % 2 == 0
        assert len(chunk) <= 520
    assert chunks[-1].endswith("конец")


def test_split_hard_wraps_long_lines():
    chunks = split_markdown("x" * 1200, limit=500)
    assert "".join(chunks) == "x" * 1200


def test_preview_keeps_tail():
    assert preview("abc", limit=10) == "abc"
    assert preview("0123456789XYZ", limit=5) == "…89XYZ"
