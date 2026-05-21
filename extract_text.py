#!/usr/bin/env python3
"""Fetch a webpage and print its visible text content for LLM consumption."""

import argparse
import re
import sys
from html.parser import HTMLParser
from html import unescape

import requests


SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}
BLOCK_TAGS = {
    "p", "div", "section", "article", "header", "footer", "nav", "aside",
    "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "tr", "td", "th",
    "table", "br", "hr", "pre", "blockquote", "form", "main",
}


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []
        self._title: str | None = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth > 0:
            return
        if self._in_title:
            if self._title is None:
                self._title = data.strip()
            return
        self._parts.append(data)

    def get_text(self) -> tuple[str | None, str]:
        text = "".join(self._parts)
        text = unescape(text)
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return self._title, text.strip()


def fetch(url: str, timeout: int) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Webpage URL to extract text from")
    parser.add_argument("--timeout", type=int, default=20, help="Request timeout in seconds")
    parser.add_argument("--max-chars", type=int, default=0, help="Truncate output to N chars (0 = no limit)")
    args = parser.parse_args()

    try:
        html = fetch(args.url, args.timeout)
    except requests.RequestException as e:
        print(f"Error fetching {args.url}: {e}", file=sys.stderr)
        return 1

    extractor = TextExtractor()
    extractor.feed(html)
    title, body = extractor.get_text()

    output_parts = [f"URL: {args.url}"]
    if title:
        output_parts.append(f"TITLE: {title}")
    output_parts.append("")
    output_parts.append(body)
    output = "\n".join(output_parts)

    if args.max_chars and len(output) > args.max_chars:
        output = output[: args.max_chars] + "\n\n[...truncated]"

    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
