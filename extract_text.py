#!/usr/bin/env python3
"""Fetch a webpage and print its visible text content for LLM consumption.

By default uses `requests`. If the response looks blocked (403, Access Denied,
Cloudflare/Akamai challenge), it falls back to a Playwright headless browser.
Pass --browser to force browser mode.
"""

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

BLOCK_MARKERS = (
    "access denied",
    "are you a human",
    "verify you are human",
    "just a moment",
    "attention required",
    "cf-browser-verification",
    "cf-chl-",
    "captcha",
)


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


def looks_blocked(html: str, status: int) -> bool:
    if status in (401, 403, 429, 503):
        return True
    head = html[:4000].lower()
    return any(m in head for m in BLOCK_MARKERS)


def fetch_requests(url: str, timeout: int) -> tuple[int, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    return resp.status_code, resp.text


def fetch_browser(url: str, timeout: int, wait_until: str = "networkidle") -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "playwright not installed. Run: pip install playwright && python3 -m playwright install chromium"
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            timezone_id="Asia/Kolkata",
            ignore_https_errors=True,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            },
        )
        context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            window.chrome = { runtime: {} };
            const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
            if (originalQuery) {
              window.navigator.permissions.query = (p) =>
                p.name === 'notifications'
                  ? Promise.resolve({ state: Notification.permission })
                  : originalQuery(p);
            }
            """
        )
        page = context.new_page()
        try:
            page.goto(url, wait_until=wait_until, timeout=timeout * 1000)
        except Exception:
            # networkidle can time out on heavy pages; settle for what we have
            pass

        html = ""
        for _ in range(5):
            try:
                page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            try:
                html = page.content()
                break
            except Exception:
                page.wait_for_timeout(1500)
        if not html:
            html = page.evaluate("document.documentElement.outerHTML")
        browser.close()
        return html


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Webpage URL to extract text from")
    parser.add_argument("--timeout", type=int, default=30, help="Timeout in seconds")
    parser.add_argument("--max-chars", type=int, default=0, help="Truncate output to N chars (0 = no limit)")
    parser.add_argument("--browser", action="store_true", help="Force headless browser (Playwright)")
    parser.add_argument("--no-fallback", action="store_true", help="Don't auto-fallback to browser when blocked")
    args = parser.parse_args()

    html: str | None = None
    used = "requests"

    if args.browser:
        print("[fetching via headless browser...]", file=sys.stderr)
        html = fetch_browser(args.url, args.timeout)
        used = "browser"
    else:
        try:
            status, html = fetch_requests(args.url, args.timeout)
        except requests.RequestException as e:
            print(f"requests failed: {e}", file=sys.stderr)
            status, html = 0, ""
        if (not html or looks_blocked(html, status)) and not args.no_fallback:
            print(f"[requests got status={status} or block page — retrying with browser...]", file=sys.stderr)
            html = fetch_browser(args.url, args.timeout)
            used = "browser"
        elif not html:
            return 1

    extractor = TextExtractor()
    extractor.feed(html)
    title, body = extractor.get_text()

    output_parts = [f"URL: {args.url}", f"FETCHED_VIA: {used}"]
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
