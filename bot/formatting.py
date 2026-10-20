"""Markdown from Claude -> the HTML subset Telegram accepts."""

import html
import re

TG_LIMIT = 4096
CHUNK = 3500  # leaves headroom for tags and escaping added by to_html()

_FENCE = re.compile(r"```([\w+#.-]*)[^\n]*\n(.*?)(?:```|\Z)", re.S)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.S)
_ITALIC = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])|(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)")
_STRIKE = re.compile(r"~~(.+?)~~", re.S)
_HEADER = re.compile(r"^#{1,6}\s+(.+?)\s*#*$", re.M)
_BULLET = re.compile(r"^(\s*)[-*+]\s+", re.M)
_HR = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$", re.M)


def to_html(md: str) -> str:
    stash: list[str] = []

    def keep(fragment: str) -> str:
        stash.append(fragment)
        return f"\x00{len(stash) - 1}\x00"

    def fence(m: re.Match) -> str:
        lang, code = m.group(1), m.group(2).rstrip("\n")
        cls = f' class="language-{html.escape(lang)}"' if lang else ""
        return keep(f"<pre><code{cls}>{html.escape(code, quote=False)}</code></pre>")

    text = _FENCE.sub(fence, md)
    text = _INLINE_CODE.sub(lambda m: keep(f"<code>{html.escape(m.group(1), quote=False)}</code>"), text)
    text = _LINK.sub(
        lambda m: keep(f'<a href="{html.escape(m.group(2))}">{html.escape(m.group(1), quote=False)}</a>'),
        text,
    )

    text = html.escape(text, quote=False)
    text = _HR.sub("──────────", text)
    text = _BULLET.sub(lambda m: f"{m.group(1)}• ", text)
    text = _HEADER.sub(r"<b>\1</b>", text)
    text = _BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = _ITALIC.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", text)
    text = _STRIKE.sub(r"<s>\1</s>", text)
    text = _blockquotes(text)

    return re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)


def _blockquotes(text: str) -> str:
    out, quote = [], []
    for line in text.split("\n"):
        if line.startswith("&gt;"):
            quote.append(line[4:].removeprefix(" "))
            continue
        if quote:
            out.append(f"<blockquote>{chr(10).join(quote)}</blockquote>")
            quote = []
        out.append(line)
    if quote:
        out.append(f"<blockquote>{chr(10).join(quote)}</blockquote>")
    return "\n".join(out)


def split_markdown(md: str, limit: int = CHUNK) -> list[str]:
    """Split on line boundaries, closing and reopening code fences across chunks."""
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    fence: str | None = None  # opening line of the fence we are inside, if any

    def flush() -> None:
        nonlocal buf, size
        if not buf:
            return
        if fence:
            buf.append("```")
        chunks.append("\n".join(buf).strip("\n"))
        buf = [fence] if fence else []
        size = len(fence) + 1 if fence else 0

    for line in md.split("\n"):
        while len(line) > limit:
            flush()
            buf.append(line[:limit])
            size += limit + 1
            line = line[limit:]
        if size + len(line) + 1 > limit:
            flush()
        buf.append(line)
        size += len(line) + 1
        if line.lstrip().startswith("```"):
            fence = None if fence else line.strip()

    if fence:
        fence = None  # unterminated fence: to_html() treats end of text as its close
    flush()
    return [c for c in chunks if c.strip()] or [""]


def preview(text: str, limit: int = TG_LIMIT - 96) -> str:
    """Plain-text tail of a reply that is still streaming."""
    return text if len(text) <= limit else "…" + text[-limit:]
