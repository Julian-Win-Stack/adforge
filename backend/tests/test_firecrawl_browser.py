"""The marked screenshot's browser steps (firecrawl.ACTIONS), run in a real Chromium on a
shop page served here, as Firecrawl runs them in its own browser: the waits are cut short,
since the page is served locally."""

import io
import json
from collections.abc import Iterator
from typing import Any

import PIL.Image
import pytest
from playwright.sync_api import Page, ViewportSize, sync_playwright
from pytest_httpserver import HTTPServer

from jobs import firecrawl

from .conftest import picture

# Firecrawl's browser is as wide as a desktop screen.
WINDOW: ViewportSize = {"width": 1920, "height": 1080}
# Every wait in the steps is cut to at most this, in milliseconds.
LONGEST_WAIT_MS = 300

RED = (220, 30, 30)
BLUE = (30, 60, 220)

# A shop page that does what smartish.com does (seen 2026-10-15 on the Gripmunk case's page):
# its first script, when the browser says it runs on Linux as page-speed testers do, holds
# back every script after it, so they never run. The held-back scripts build the photo
# gallery, whose photos are cell backgrounds, hidden until the gallery is built. One of them
# is gone from the shop.
HELD_BACK_PAGE = """<!doctype html>
<html>
<head>
  <title>Gripmunk Clear Case</title>
  <script>
    if (navigator.platform == "Linux x86_64") {
      var uLTS = new MutationObserver((changes) => {
        changes.forEach(({ addedNodes }) => {
          addedNodes.forEach((node) => {
            if (node.nodeType === 1 && node.tagName === "SCRIPT") {
              node.setAttribute("data-src", node.src);
              node.removeAttribute("src");
              node.type = "text/lazyload";
            }
          });
        });
      });
      uLTS.observe(document.documentElement, { childList: true, subtree: true });
    }
  </script>
  <style>
    .cell { visibility: hidden; width: 600px; height: 450px; background-size: cover; }
    .cell.built { visibility: visible; }
  </style>
  <script src="/gone.js"></script>
  <script src="/gallery.js"></script>
</head>
<body>
  <h1>Gripmunk Clear Case</h1>
  <p>$14.99</p>
  <div class="gallery">
    <div class="cell" aria-label="Front"
         style="background:url('/cdn/shop/files/front_1410x580.png')"></div>
    <div class="cell" style="background:url('/cdn/shop/files/back_1410x580.png')"></div>
  </div>
  <script>
    document.addEventListener("DOMContentLoaded", () => Gallery.build());
  </script>
</body>
</html>
"""
GALLERY_JS = "window.Gallery = { build() { document.querySelectorAll('.cell')"
GALLERY_JS += ".forEach((cell) => cell.classList.add('built')); } };"


@pytest.fixture(scope="module")
def browser_page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser.new_page(viewport=WINDOW)
        browser.close()


def run_actions(page: Page, url: str) -> tuple[dict[str, Any], PIL.Image.Image]:
    """Open `url` and run the marked screenshot's steps on it, as Firecrawl does: its answer's
    `actions` (the scripts' returns) and the screenshot."""
    page.goto(url)
    returns: list[dict[str, Any]] = []
    shot = b""
    for action in firecrawl.ACTIONS:
        match action["type"]:
            case "wait":
                page.wait_for_timeout(min(action["milliseconds"], LONGEST_WAIT_MS))
            case "scroll":
                page.mouse.wheel(0, WINDOW["height"])
            case "executeJavascript":
                returns.append({"type": "string", "value": page.evaluate(action["script"])})
            case "screenshot":
                shot = page.screenshot(full_page=action["fullPage"])
    return {"actions": {"javascriptReturns": returns}}, PIL.Image.open(io.BytesIO(shot))


@pytest.fixture
def held_back_shop(httpserver: HTTPServer) -> str:
    httpserver.expect_request("/products/case").respond_with_data(
        HELD_BACK_PAGE, content_type="text/html"
    )
    httpserver.expect_request("/gone.js").respond_with_data("Not found", status=404)
    httpserver.expect_request("/gallery.js").respond_with_data(
        GALLERY_JS, content_type="text/javascript"
    )
    for name, colour in (("front", RED), ("back", BLUE)):
        httpserver.expect_request(f"/cdn/shop/files/{name}_1410x580.png").respond_with_data(
            picture(600, 450, colour), content_type="image/png"
        )
    return httpserver.url_for("/products/case")


def test_a_gallery_a_shop_holds_back_from_linux_browsers_is_shown_and_numbered(
    browser_page: Page, held_back_shop: str
) -> None:
    answer, shot = run_actions(browser_page, held_back_shop)

    marks = firecrawl.marks_in(answer)
    numbered = {
        picture["src"].split("/")[-1]: picture
        for picture in marks["imgs"]
        if isinstance(picture.get("n"), int)
    }
    assert {name: picture["alt"] for name, picture in numbered.items()} == {
        "front_1410x580.png": "Front",
        "back_1410x580.png": "",
    }
    for name, colour in (("front_1410x580.png", RED), ("back_1410x580.png", BLUE)):
        box = numbered[name]
        middle = (box["x"] + box["w"] // 2, box["y"] + box["h"] // 2)
        assert shot.convert("RGB").getpixel(middle) == colour, json.dumps(box)


# A page that holds nothing back: its scripts run once, as always. Its placeholder background
# isn't a photo.
PLAIN_PAGE = """<!doctype html>
<html>
<head><title>Stoneware Mug</title><script>window.runs = (window.runs || 0) + 1;</script></head>
<body>
  <h1>Stoneware Mug</h1>
  <img src="/cdn/mug.png" alt="The mug" width="300" height="300">
  <div style="width:300px; height:300px;
              background:url('data:image/gif;base64,R0lGODlhAQABAAAAACw=')"></div>
</body>
</html>
"""


def test_a_page_that_holds_nothing_back_runs_its_scripts_once_and_its_photos_are_numbered(
    browser_page: Page, httpserver: HTTPServer
) -> None:
    httpserver.expect_request("/products/mug").respond_with_data(
        PLAIN_PAGE, content_type="text/html"
    )
    httpserver.expect_request("/cdn/mug.png").respond_with_data(
        picture(300, 300, RED), content_type="image/png"
    )

    answer, _ = run_actions(browser_page, httpserver.url_for("/products/mug"))

    assert browser_page.evaluate("window.runs") == 1
    numbered = [p for p in firecrawl.marks_in(answer)["imgs"] if isinstance(p.get("n"), int)]
    assert [(p["n"], p["alt"], p["src"].split("/")[-1]) for p in numbered] == [
        (1, "The mug", "mug.png")
    ]
