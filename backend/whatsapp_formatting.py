"""Deterministic Markdown -> WhatsApp presentation, independent of the LLM."""
import html
from html.parser import HTMLParser
import re
import unicodedata
from urllib.parse import urlsplit

import regex


class _PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if self.hidden:
            return
        if tag in {"br", "p", "div", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")
        if tag in {"strong", "b"}:
            self.parts.append("*")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
            return
        if not self.hidden:
            if tag in {"strong", "b"}:
                self.parts.append("*")
            if tag in {"p", "div", "li", "h1", "h2", "h3"}:
                self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def format_whatsapp(text: str) -> str:
    """Preserve information, menu paths, numbers and URLs; never invent content."""
    text = unicodedata.normalize("NFC", text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"TRANSFERIR_SUPORTE|<REQUIRES_ESCALATION>", "", text, flags=re.I)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    protected = []

    def protect(value):
        protected.append(value)
        return f"\ue000{len(protected) - 1}\ue001"

    # Balanced parentheses are common in article URLs.
    link_pattern = r"!?\[([^\]\n]+)\]\(([^\s()]*(?:\([^()]*\)[^\s()]*)*)\)"
    def link(match):
        label, url = match.groups()
        parsed = urlsplit(url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username:
            return label
        return f"{label}\n{protect(html.unescape(url))}"

    text = re.sub(link_pattern, link, text)
    text = re.sub(r"https?://[^\s<>]+", lambda m: protect(html.unescape(m[0])), text)
    text = re.sub(r"```[^\n`]*\n([\s\S]*?)```", lambda m: protect("```\n" + m[1].strip() + "\n```"), text)
    text = re.sub(r"`([^`\n]+)`", lambda m: protect(m[0]), text)
    if re.search(r"</?(?:p|div|br|strong|b|li|ul|ol|h[1-6]|script|style)\b", text, re.I):
        parser = _PlainHTML()
        parser.feed(text)
        text = "".join(parser.parts)
    text = html.unescape(text)
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"*\1*", text)
    text = re.sub(r"__([^_\n]+)__", r"*\1*", text)
    text = re.sub(r"~~([^~\n]+)~~", r"~\1~", text)
    text = re.sub(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", lambda m: "*" + m[1].strip("*") + "*", text)
    text = re.sub(r"(?m)^\s*(?:[-*_]\s*){3,}$", "", text)
    text = re.sub(r"(?m)^\s*[•●]\s+", "- ", text)
    text = re.sub(r"(?m)^\s*\*\s+", "- ", text)
    text = re.sub(r"(?m)^(\s*\d+)\)\s+", r"\1. ", text)
    text = re.sub(r"(?<=[^\s])[ \t]+(?=[1-9]️⃣|🔟)", "\n\n", text)
    # Tables become labeled rows readable on a narrow mobile screen.
    lines, output = text.splitlines(), []
    i = 0
    while i < len(lines):
        if i + 1 < len(lines) and "|" in lines[i] and re.fullmatch(r"[\s|:-]+", lines[i + 1]) and "-" in lines[i + 1]:
            headers = [c.strip().strip("*") for c in lines[i].strip().strip("|").split("|")]
            i += 2
            while i < len(lines) and "|" in lines[i]:
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                output.append("\n".join(f"*{headers[n]}:* {cell}" if n < len(headers) else cell for n, cell in enumerate(cells)))
                output.append("")
                i += 1
            continue
        line = lines[i].rstrip()
        if re.match(r"^(?:\d+\. |[1-9]️⃣|🔟)", line) and output and output[-1]:
            output.append("")
        output.append(line)
        i += 1
    text = re.sub(r"\n[ \t]+\n", "\n\n", "\n".join(output))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    for n, value in enumerate(protected):
        text = text.replace(f"\ue000{n}\ue001", value)
    return text


def message_length(text: str) -> int:
    """Conservative UTF-16 units, including both halves of emoji surrogates."""
    return len(text.encode("utf-16-le")) // 2


def split_whatsapp(text: str, max_length: int = 3500) -> list[str]:
    """Split at paragraphs/lines/words without cutting links or emoji clusters."""
    if max_length < 32 or max_length > 4096:
        raise ValueError("max_length must be between 32 and 4096")
    text = format_whatsapp(text)
    chunks = []
    while message_length(text) > max_length:
        clusters = list(regex.finditer(r"\X", text))
        units, end = 0, 0
        for cluster in clusters:
            units += message_length(cluster[0])
            if units > max_length:
                break
            end = cluster.end()
        # Keep links and ordinary emphasis together. Oversized code/emphasis
        # is unwrapped before splitting; the textual content is retained.
        spans = list(re.finditer(r"https?://\S+|```[\s\S]*?```|`[^`\n]+`|\*[^*\n]+\*|_[^_\n]+_|~[^~\n]+~", text))
        for span in spans:
            if span.start() < end < span.end():
                if span.start() == 0:
                    if span[0].startswith(("https://", "http://")):
                        raise ValueError("A URL exceeds the channel message limit")
                    text = span[0].strip("`*_~") + text[span.end():]
                    break
                end = span.start()
        else:
            candidates = [m for m in re.finditer(r"\n\n|\n|[ \t]+", text[:end + 1])
                          if m.start() > 0 and m.end() <= end
                          and not any(s.start() < m.start() < s.end() for s in spans)]
            for separator in ("\n\n", "\n", " "):
                matching = [m for m in candidates if (m[0] == separator or separator == " " and not m[0].strip()) and m.start() >= end // 2]
                if matching:
                    end = matching[-1].start()
                    break
            if not end:
                raise ValueError("A character exceeds the channel message limit")
            chunks.append(text[:end].strip())
            text = text[end:].lstrip()
            continue
        # Retry after unwrapping an oversized formatting span.
    if text:
        chunks.append(text)
    return chunks


def whatsapp_rich_text(text: str) -> str:
    """Safe HTML fallback for HubSpot, never interpreting customer HTML."""
    return "<p>" + html.escape(text).replace("\n", "<br>") + "</p>"
