import io
import json
import random
import re
import socket
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

import PIL.Image
import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from pydantic import BaseModel
from pytest_django import Settings
from pytest_httpserver import HTTPServer
from rest_framework.test import APIClient
from werkzeug import Request, Response

from adforge import celery_app
from agents import tasks
from agents.models import ToolCall
from chat.models import Session
from gateway.fake import FakeModel, turn
from gateway.gateway import use_model
from gateway.models import ModelCall
from gateway.openai_adapter import OpenAIProvider
from gateway.types import ModelReply, ModelRequest, TurnReply, TurnRequest
from jobs.models import BROLL_FIELDS, Job

celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)


def picture(width: int, height: int, colour: tuple[int, int, int], format: str = "PNG") -> bytes:
    """A real image file of one colour."""
    file = io.BytesIO()
    PIL.Image.new("RGB", (width, height), colour).save(file, format=format)
    return file.getvalue()


# The mug's photos, told apart by colour. The side one is as big as a phone takes them.
MUG_FRONT = picture(400, 300, (143, 170, 140))  # sage green
MUG_SIDE = picture(1600, 1200, (236, 229, 206))  # cream

PRODUCT_PAGE = """<!doctype html>
<html>
<head>
  <title>Stoneware Mug | Kiln & Co</title>
  <meta property="og:image" content="{side}">
  <script type="application/ld+json">
    {{"@context": "https://schema.org", "@type": "Product", "name": "Stoneware Mug",
      "image": ["/cdn/mug-front.png", "{side}"],
      "brand": {{"@type": "Brand", "name": "Kiln & Co"}},
      "offers": {{"@type": "Offer", "price": "24.00", "priceCurrency": "USD",
                  "availability": "https://schema.org/InStock"}}}}
  </script>
  <script>window.analytics = "tracking code, not page text";</script>
  <style>.price {{ color: red; }}</style>
</head>
<body>
  <h1>Stoneware Mug</h1>
  <p class="price">$24.00</p>
  <p>Hand-thrown, holds 350 ml, dishwasher safe.</p>
</body>
</html>
"""


# What the page check answers for the mug's page.
READABLE = {"decision": "readable", "reason": "The page names the mug, its price and its size."}

# What the copy model copies out of the mug's page: all of it but the price.
COPIED = {
    "product": "Stoneware Mug",
    "passages": ["Stoneware Mug", "Hand-thrown, holds 350 ml, dishwasher safe."],
}

# What the photo picker answers when a test doesn't say: on the shampoo's marked
# screenshot, its big gallery photo (I8) and the same photo's thumbnail (I16).
SHAMPOO_PICKED = {
    "product": "Detox Clarifying Hair Shampoo",
    "gallery_images": [8, 16],
    "more_images": [],
    "product_sections": [15, 30],
    "notes": "",
    "face_images": [],
}

# What the Face-note call answers for a photo with no stranger's face in it.
NO_FACE: dict[str, Any] = {"has_face": False}

# What the producer plans for the mug's page: three scenes.
PLAN: dict[str, Any] = {
    "decision": "plan",
    "reason": "Three scenes: what the mug is, what it's like to use, and its price.",
    "question": None,
    "plan": {
        "scenes": [
            {"line": "Meet the Stoneware Mug from Kiln & Co.", "overlay": "Kiln & Co"},
            {"line": "Hand-thrown, holds 350 ml, and dishwasher safe.", "overlay": None},
            {"line": "Yours for $24.00.", "overlay": "$24.00"},
        ],
        "product_name": "Stoneware Mug",
        "product_colour": "sage green",
        "colour_photos": [1],
        "product_size": "handheld",
        "person_gender": "woman",
        "person_looks": "A potter in her thirties in a linen apron, in a sunny workshop.",
        "person_voice": "A warm, relaxed woman in her thirties with a soft British accent.",
    },
}
# The plan's 18 words take the fake voice 9 seconds: it speaks 2 words a second.


# What the planner says about a B-roll scene when a test doesn't say: it shows the mug at
# its best, with no face in it, and needs nothing the main photo can't show.
SHOWCASE: dict[str, Any] = {
    "broll_kind": "showcase",
    "person_shown": "no face",
    "usage": None,
    "result": None,
    "needs": [],
}


def broll_labels() -> list[tuple[str, str, str, str, list[dict[str, Any]]]]:
    """Each stored scene's B-roll kind, person, usage, result and needs, in order."""
    return list(Job.objects.get().scenes.values_list(*BROLL_FIELDS))


def broll(scene: dict[str, Any]) -> dict[str, Any]:
    """A planned scene with SHOWCASE's B-roll labels when it shows something, as every
    B-roll scene must have them; a scene the person says to camera as it is."""
    shows = scene.get("shows")
    return {**SHOWCASE, **scene} if shows and shows.strip() else scene


def facts_ok(*scenes: int) -> dict[str, Any]:
    """What the fact check answers when every one of `scenes` matches the page."""
    return {
        "decision": "checked",
        "reason": "Every claim is stated on the page.",
        "question": None,
        "lines": [
            {"scene": scene, "verdict": "ok", "wrong": None, "problem": None, "page_says": None}
            for scene in scenes
        ],
    }


# The mug plan's fact check, when all three lines match the page.
FACTS_OK = facts_ok(1, 2, 3)

# A B-roll starting picture's check, when it finds nothing wrong.
PICTURE_OK: dict[str, Any] = {
    check: {"passes": True, "problem": None}
    for check in (
        "real_objects",
        "matches_the_shop_photo",
        "label_turns_with_the_product",
        "shows_the_before",
        "no_face",
        "prompts_agree",
        "makes_sense_for_the_line",
    )
}


def a_plan_with(**changes: Any) -> dict[str, Any]:
    """PLAN with some of its plan's fields replaced."""
    return {**PLAN, "plan": {**PLAN["plan"], **changes}}


def plan_with(*lines: str) -> dict[str, Any]:
    """PLAN with these lines for its scenes, each said by the person to camera, and the
    product's name as "mug", which the first of them must say."""
    scenes = [{"line": line} for line in lines]
    return {**PLAN, "plan": {**PLAN["plan"], "scenes": scenes, "product_name": "mug"}}


# The planning checks' arguments when the shop owner has made no choice.
NO_CHOICES: dict[str, Any] = {"line_choices": [], "length_choice": None}


@pytest.fixture(autouse=True)
def _isolated_outside_world(settings: Settings, tmp_path: Path) -> None:
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.RETRY_DELAYS_SECONDS = [0, 0]
    settings.CLIP_POLL_SECONDS = 0
    # The test shop runs on this machine, an address real jobs are never allowed to fetch.
    settings.FETCH_PRIVATE_ADDRESSES = True
    # Nothing is sent to Langfuse, even from a machine whose environment holds real keys.
    settings.LANGFUSE_PUBLIC_KEY = ""
    settings.LANGFUSE_SECRET_KEY = ""
    # Pages are read with the plain download, never by the real Firecrawl. A test that reads
    # through Firecrawl uses the `firecrawl` fixture's stand-in.
    settings.FIRECRAWL_API_KEY = ""


@pytest.fixture
def api() -> APIClient:
    return APIClient()


@pytest.fixture
def fake_model() -> Iterator[FakeModel]:
    fake = FakeModel()
    fake.answer_unscripted("copy_page_text", copy_every_line)
    fake.answer_unscripted("pick_photos", lambda _: SHAMPOO_PICKED)
    fake.answer_unscripted("note_face", lambda _: NO_FACE)
    fake.answer_unscripted("check_starting_picture", lambda _: PICTURE_OK)
    with use_model(fake):
        yield fake


def copy_every_line(request: ModelRequest[Any]) -> dict[str, Any]:
    """What the copy model answers when a test doesn't say: every line of the page it was
    given, so the product's own text is the whole page, as before it was copied out."""
    given = request.handoff.model_dump()
    return {"product": given["product"], "passages": given["page_text"].splitlines()}


@pytest.fixture
def product_page_url(httpserver: HTTPServer) -> str:
    """A shop served from a real local web server, with one product page and its photos."""
    side = httpserver.url_for("/cdn/mug-side.png")
    httpserver.expect_request("/products/mug").respond_with_data(
        PRODUCT_PAGE.format(side=side), content_type="text/html; charset=utf-8"
    )
    httpserver.expect_request("/cdn/mug-front.png").respond_with_data(
        MUG_FRONT, content_type="image/png"
    )
    httpserver.expect_request("/cdn/mug-side.png").respond_with_data(
        MUG_SIDE, content_type="image/png"
    )
    return httpserver.url_for("/products/mug")


# A real answer of Firecrawl's page read, for OUAI's Detox Shampoo, trimmed to the product's
# own text and one "Pairs Well With" product.
FIRECRAWL_PAGE_READ = (Path(__file__).parent / "fixtures" / "firecrawl_page_read.json").read_text()
# The shampoo's photo, as the page declares it twice: at full size, and 1,920 px wide.
SHAMPOO_PHOTO = picture(300, 400, (201, 141, 60), format="JPEG")
SHAMPOO_PHOTO_PATH = (
    "/cdn/shop/files/Update_2_DetoxShampoo_260611-17-23_Site_Asset_PDP_Product_"
    "Thumbnail_1440x1780_11.jpg"
)


# Real answers of Firecrawl's other two calls for the same page, trimmed: the marked
# screenshot's (its marking script's list cut to six pictures and five text pieces, and the
# HTML and markdown it also sends left out), and the product record's (two images a size).
FIRECRAWL_MARKED = (Path(__file__).parent / "fixtures" / "firecrawl_marked.json").read_text()
FIRECRAWL_PRODUCT = (Path(__file__).parent / "fixtures" / "firecrawl_product.json").read_text()
# The record's first official photo, R1, where the shop's CDN keeps it.
SHAMPOO_RECORD_PHOTO_PATH = (
    "/s/files/1/1043/7322/files/Update_2_DetoxShampoo_260611-17-23_Site_Asset_PDP_Product_"
    "Thumbnail_1440x1780_11.jpg"
)


def screenshot(width: int, height: int) -> bytes:
    """A page screenshot, dark at the top and light at the bottom, so no part is blank."""
    file = io.BytesIO()
    PIL.Image.linear_gradient("L").resize((width, height)).convert("RGB").save(file, "PNG")
    return file.getvalue()


def photo(seed: int, width: int = 64, height: int = 80) -> bytes:
    """A photo of its own: random dots, so no two seeds look alike to the copy check, and
    one seed looks the same at every size."""
    dots = random.Random(seed).randbytes(16 * 20 * 3)
    small = PIL.Image.frombytes("RGB", (16, 20), dots)
    file = io.BytesIO()
    small.resize((width, height), PIL.Image.Resampling.NEAREST).save(file, "PNG")
    return file.getvalue()


type FirecrawlCall = Literal["page", "marked", "product"]


@pytest.fixture(scope="session")
def make_httpserver() -> Iterator[HTTPServer]:
    """The local web server, answering requests at the same time as real servers do: the
    page is read with Firecrawl's three calls at once, and a slow one mustn't hold up the
    others."""
    server = HTTPServer(threaded=True)
    server.start()
    yield server
    server.clear()
    if server.is_running():
        server.stop()


class FakeFirecrawl:
    """A stand-in Firecrawl on the local web server. It answers /v2/scrape by what the
    request asks for, as the real one does: a page read (markdown and rawHtml), a marked
    screenshot (actions) and a product record each get the saved real answer, with the shop's
    address swapped for the local shop's. The screenshot is served from the local server."""

    def __init__(self, httpserver: HTTPServer) -> None:
        self.shop = httpserver.url_for("").rstrip("/")
        # What every request asked for, oldest first.
        self.requests: list[dict[str, Any]] = []
        # Answers to give each call before the real ones, such as "busy": each (status, body,
        # headers).
        self.first: dict[FirecrawlCall, list[tuple[int, dict[str, Any], dict[str, str]]]] = {
            "page": [],
            "marked": [],
            "product": [],
        }
        # How long the page read takes to answer, for a test of Firecrawl timing out.
        self.takes_seconds = 0.0
        # Each call's answer, for a test to change, such as the shop's status code.
        self.page_read: dict[str, Any] = json.loads(
            re.sub(r"https?://theouai\.com", self.shop, FIRECRAWL_PAGE_READ)
        )
        shop_links = r"(?:https?:)?//(?:theouai\.com|cdn\.shopify\.com)"
        self.marked: dict[str, Any] = json.loads(re.sub(shop_links, self.shop, FIRECRAWL_MARKED))
        self.marked["data"]["actions"]["screenshots"] = [f"{self.shop}/firecrawl/screenshot.png"]
        self.product: dict[str, Any] = json.loads(re.sub(shop_links, self.shop, FIRECRAWL_PRODUCT))
        # 2,600 px tall: three parts of 1,200 px.
        self.screenshot = screenshot(480, 2_600)
        httpserver.expect_request("/v2/scrape", method="POST").respond_with_handler(self._answer)
        httpserver.expect_request("/firecrawl/screenshot.png").respond_with_handler(
            lambda _: Response(self.screenshot, content_type="image/png")
        )

    def answers_first(
        self,
        status: int,
        error: str,
        *,
        times: int = 1,
        call: FirecrawlCall = "page",
        headers: dict[str, str] | None = None,
    ) -> None:
        """Have the next `times` requests for `call` answered with an error, as when Firecrawl
        is busy."""
        self.first[call] += [(status, {"success": False, "error": error}, headers or {})] * times

    def asked(self, call: FirecrawlCall) -> list[dict[str, Any]]:
        """What every request for `call` asked for, oldest first."""
        return [asked for asked in self.requests if _which_call(asked) == call]

    def _answer(self, request: Request) -> Response:
        if request.headers.get("Authorization") != "Bearer fc-test":
            return Response(json.dumps({"success": False, "error": "Unauthorized"}), status=401)
        asked = request.get_json()
        self.requests.append(asked)
        call = _which_call(asked)
        if call == "page":
            time.sleep(self.takes_seconds)
        answers = {"page": self.page_read, "marked": self.marked, "product": self.product}
        headers: dict[str, str] = {}
        if call is None:
            status, body = 400, {"success": False, "error": f"Unknown formats {asked['formats']}"}
        elif self.first[call]:
            status, body, headers = self.first[call].pop(0)
        else:
            status, body = 200, answers[call]
        return Response(
            json.dumps(body), status=status, headers=headers, content_type="application/json"
        )


def _which_call(asked: dict[str, Any]) -> FirecrawlCall | None:
    if "actions" in asked:
        return "marked"
    if asked["formats"] == ["product"]:
        return "product"
    if set(asked["formats"]) == {"markdown", "rawHtml"}:
        return "page"
    return None


@pytest.fixture
def firecrawl(httpserver: HTTPServer, settings: Settings) -> FakeFirecrawl:
    """Pages are read through a stand-in Firecrawl, set up with a key as a real server is."""
    settings.FIRECRAWL_API_KEY = "fc-test"
    settings.FIRECRAWL_URL = httpserver.url_for("")
    return FakeFirecrawl(httpserver)


@pytest.fixture
def shampoo_page_url(httpserver: HTTPServer, firecrawl: FakeFirecrawl) -> str:
    """The shampoo's page on the local shop, as a plain download gets it, with its photo.
    Gives the link."""
    httpserver.expect_request("/products/detox-shampoo").respond_with_data(
        firecrawl.page_read["data"]["rawHtml"], content_type="text/html; charset=utf-8"
    )
    httpserver.expect_request(SHAMPOO_PHOTO_PATH).respond_with_data(
        SHAMPOO_PHOTO, content_type="image/jpeg"
    )
    httpserver.expect_request(SHAMPOO_RECORD_PHOTO_PATH).respond_with_data(
        SHAMPOO_PHOTO, content_type="image/jpeg"
    )
    return httpserver.url_for("/products/detox-shampoo")


@pytest.fixture
def session_id(api: APIClient) -> str:
    response = api.post("/api/sessions/", {}, format="json")
    assert response.status_code == 201, response.json()
    started: str = response.json()["id"]
    return started


@pytest.fixture
def say(api: APIClient, session_id: str) -> Callable[..., None]:
    """Send the user's message through the chat, with any photos attached as (name,
    content). The producer runs before the request returns, so script its turns first."""

    def sending(text: str, *photos: tuple[str, bytes]) -> None:
        if photos:
            attached = [
                SimpleUploadedFile(name, content, content_type="image/png")
                for name, content in photos
            ]
            sent = api.post(
                f"/api/sessions/{session_id}/messages/",
                {"text": text, "photos": attached},
                format="multipart",
            )
        else:
            sent = api.post(f"/api/sessions/{session_id}/messages/", {"text": text}, format="json")
        assert sent.status_code == 201, sent.json()

    return sending


def chat(api: APIClient, session_id: str) -> list[tuple[str, str]]:
    """The conversation as the browser shows it: who said what."""
    messages = api.get(f"/api/sessions/{session_id}/messages/").json()
    return [(message["role"], message["text"]) for message in messages]


def given_to_the_producer(number: int) -> list[dict[str, Any]]:
    """What the producer's model was given on its `number`th turn (from 1), as recorded."""
    calls = ModelCall.objects.filter(purpose="produce").order_by("created_at", "id")
    conversation: list[dict[str, Any]] = calls[number - 1].handoff["conversation"]
    return conversation


def producer_turns() -> int:
    """How many turns the producer's model has taken, over every producer that ran."""
    return ModelCall.objects.filter(purpose="produce").count()


def results_of(tool: str) -> list[str]:
    """What each call of `tool` handed back to the producer, oldest first."""
    return list(ToolCall.objects.filter(tool=tool).values_list("result", flat=True))


def paid_for() -> list[str]:
    """The purpose of every model call the tools made, oldest first: the producer's own
    turns aside."""
    return list(
        ModelCall.objects.exclude(purpose="produce")
        .order_by("created_at", "id")
        .values_list("purpose", flat=True)
    )


def handoffs(purpose: str) -> list[dict[str, Any]]:
    """What each model call for `purpose` was handed, oldest first."""
    return list(
        ModelCall.objects.filter(purpose=purpose)
        .order_by("created_at", "id")
        .values_list("handoff", flat=True)
    )


def served(link: str, settings: Settings) -> bytes:
    """What the browser gets from a link: the web server hands out MEDIA_ROOT at /media/."""
    assert link.startswith("/media/"), f"{link} isn't a link the web server hands out"
    return (Path(settings.MEDIA_ROOT) / link.removeprefix("/media/")).read_bytes()


def video(data: bytes) -> tuple[int, int, float]:
    """A video's width, height and length in seconds, as ffprobe reads it."""
    with tempfile.NamedTemporaryFile(suffix=".mp4") as file:
        file.write(data)
        file.flush()
        probed = subprocess.run(
            [settings.FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json"]
            + [file.name],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    found = json.loads(probed.stdout)
    (picture,) = [stream for stream in found["streams"] if stream["codec_type"] == "video"]
    return picture["width"], picture["height"], float(found["format"]["duration"])


def colour_at(data: bytes, seconds: float) -> str:
    """Which of the fake's clip colours a video shows `seconds` in, read from its middle."""
    with tempfile.NamedTemporaryFile(suffix=".mp4") as file:
        file.write(data)
        file.flush()
        frame = subprocess.run(
            [settings.FFMPEG, "-v", "error", "-ss", str(seconds), "-i", file.name]
            + ["-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-"],
            capture_output=True,
            timeout=60,
            check=True,
        ).stdout
    shown = PIL.Image.open(io.BytesIO(frame)).convert("RGB")
    red, green, blue = shown.getpixel((shown.width // 2, shown.height // 2))  # type: ignore[misc]
    colours = {
        "red": (255, 0, 0),
        "lime": (0, 255, 0),
        "blue": (0, 0, 255),
        "yellow": (255, 255, 0),
    }
    return min(
        colours,
        key=lambda name: sum(
            (a - b) ** 2 for a, b in zip(colours[name], (red, green, blue), strict=True)
        ),
    )


def drawn_in(data: bytes, seconds: float) -> set[str]:
    """Which bands of the picture, "top" or "bottom", have something drawn over the clip
    `seconds` in: pixels that differ from the clip's own colour, read from its middle."""
    with tempfile.NamedTemporaryFile(suffix=".mp4") as file:
        file.write(data)
        file.flush()
        frame = subprocess.run(
            [settings.FFMPEG, "-v", "error", "-ss", str(seconds), "-i", file.name]
            + ["-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-"],
            capture_output=True,
            timeout=60,
            check=True,
        ).stdout
    shown = PIL.Image.open(io.BytesIO(frame)).convert("RGB")
    width, height = shown.size
    clip_colour = _rgb(shown, width // 2, height // 2)
    band = height // 5

    def differs(x: int, y: int) -> bool:
        return sum(abs(a - b) for a, b in zip(_rgb(shown, x, y), clip_colour, strict=True)) > 60

    drawn = set()
    for name, top in (("top", 0), ("bottom", height - band)):
        if any(differs(x, y) for x in range(width) for y in range(top, top + band)):
            drawn.add(name)
    return drawn


def _rgb(image: PIL.Image.Image, x: int, y: int) -> tuple[int, int, int]:
    pixel = image.getpixel((x, y))
    assert isinstance(pixel, tuple)
    red, green, blue = pixel
    return red, green, blue


def silences(data: bytes) -> list[tuple[float, float]]:
    """Where a video's sound is silent for more than a quarter of a second, in seconds."""
    with tempfile.NamedTemporaryFile(suffix=".mp4") as file:
        file.write(data)
        file.flush()
        heard = subprocess.run(
            [settings.FFMPEG, "-i", file.name, "-af", "silencedetect=noise=-40dB:d=0.25"]
            + ["-f", "null", "-"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        ).stderr
    starts = re.findall(r"silence_start: ([\d.]+)", heard)
    ends = re.findall(r"silence_end: ([\d.]+)", heard)
    return [(round(float(s), 1), round(float(e), 1)) for s, e in zip(starts, ends, strict=True)]


def loudness(
    data: bytes, band: Literal["voice", "music"], *, between: tuple[float, float] | None = None
) -> float:
    """How loud a video's sound is, in dB, in the fake voice's band (its 440 Hz tone) or the
    fake music's (its 110 Hz hum): over the whole video, or `between` two times in seconds.
    Silence is about -91 dB."""
    # Each filter is run a few times over, so little of the other band leaks through.
    heard = {
        "voice": ",".join(["highpass=f=300"] * 2),
        "music": ",".join(["lowpass=f=200"] * 4),
    }[band]
    if between is not None:
        heard = f"atrim={between[0]}:{between[1]}," + heard
    with tempfile.NamedTemporaryFile(suffix=".mp4") as file:
        file.write(data)
        file.flush()
        measured = subprocess.run(
            [settings.FFMPEG, "-i", file.name, "-af", f"{heard},volumedetect", "-f", "null", "-"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        ).stderr
    found = re.search(r"mean_volume: (-?[\d.]+) dB", measured)
    assert found is not None, measured
    return float(found.group(1))


def lines() -> list[str]:
    """The line of each of the ad's scenes, in order."""
    return list(Job.objects.get().scenes.values_list("line", flat=True))


def a_producer_last_beat(session_id: str, *, seconds_before_it_counts_as_dead: float) -> None:
    """As if a producer is working in the session, and its last beat was this long before
    it counts as dead. Less than nothing means it already does."""
    ago = settings.PRODUCER_DEAD_AFTER_SECONDS - seconds_before_it_counts_as_dead
    Session.objects.filter(pk=session_id).update(
        producer_running=True, producer_seen_at=timezone.now() - timedelta(seconds=ago)
    )


@pytest.fixture
def page_read(fake_model: FakeModel, product_page_url: str, say: Callable[..., None]) -> str:
    """A chat whose ad has its product page read, with the page's 2 photos. Gives the link."""
    fake_model.respond(
        "produce",
        turn(calls=[("read_page", {"link": product_page_url, "target_seconds": None})]),
        turn(says="I read your mug's page."),
    )
    fake_model.respond("check_page", READABLE)
    say(f"Make an ad for {product_page_url}")
    return product_page_url


@pytest.fixture
def the_plan(request: pytest.FixtureRequest) -> dict[str, Any]:
    """What the planner answers: PLAN, unless a test parametrizes this fixture indirectly
    with a plan of its own, such as one for a large product."""
    plan: dict[str, Any] = getattr(request, "param", PLAN)
    return plan


@pytest.fixture
def planned(
    fake_model: FakeModel, page_read: str, the_plan: dict[str, Any], say: Callable[..., None]
) -> None:
    """A chat whose ad is planned: three scenes."""
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Here's the plan."))
    fake_model.respond("plan_ad", the_plan)
    say("Plan it")


def openai_reply(content: dict[str, Any], status: str = "completed") -> dict[str, Any]:
    """A Responses API reply as OpenAI sends it, billed for 1,200 tokens in and 300 out."""
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 1_789_000_000,
        "model": "gpt-5-mini",
        "status": status,
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": status,
                "content": [content],
            }
        ],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "usage": {
            "input_tokens": 1_200,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 300,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 1_500,
        },
    }


def openai_answer(answer: dict[str, Any]) -> dict[str, Any]:
    """A reply whose text is `answer` as JSON, the way structured output comes back."""
    return openai_reply({"type": "output_text", "text": json.dumps(answer), "annotations": []})


def openai_turn(says: str = "", *calls: tuple[str, str, dict[str, Any]]) -> dict[str, Any]:
    """An agent's turn as OpenAI sends it: what it says, then each tool it calls, given as
    (call id, tool, arguments)."""
    reply = openai_reply({"type": "output_text", "text": says, "annotations": []})
    if not says:
        reply["output"] = []
    reply["output"] += [
        {
            "type": "function_call",
            "id": f"fc_{call_id}",
            "call_id": call_id,
            "name": tool,
            "arguments": json.dumps(arguments),
            "status": "completed",
        }
        for call_id, tool, arguments in calls
    ]
    return reply


@pytest.fixture
def openai_server(httpserver: HTTPServer, settings: Settings) -> Iterator[Callable[..., None]]:
    """Our real OpenAI code, talking to a stand-in OpenAI server on this machine.
    Call it with the replies the server should send, one per request, in order."""
    settings.OPENAI_API_KEY = "sk-test"
    settings.OPENAI_BASE_URL = httpserver.url_for("/v1")

    def reply_with(*replies: dict[str, Any]) -> None:
        for reply in replies:
            httpserver.expect_oneshot_request("/v1/responses", method="POST").respond_with_json(
                reply
            )

    with use_model(OpenAIText()):
        yield reply_with


class OpenAIText(FakeModel):
    """Text calls and agents' turns go to our real OpenAI code; the portrait and voice are
    faked, so a test of what the models are sent needs no stand-in picture or voice service."""

    name = "openai"

    def __init__(self) -> None:
        super().__init__()
        self._openai = OpenAIProvider()

    def complete[Out: BaseModel](self, request: ModelRequest[Out]) -> ModelReply[Out]:
        return self._openai.complete(request)

    def take_turn(self, request: TurnRequest) -> TurnReply:
        return self._openai.take_turn(request)


class FakeDns:
    """What host names look up to when a job checks where a link points. Only that check
    sees these; the fetch itself still goes to the real host. A name not in `records`
    doesn't exist, and a record can be an error to raise instead of an address."""

    def __init__(self) -> None:
        self.records: dict[str, str | socket.gaierror] = {}
        self.lookups: list[str] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(socket, name)

    def getaddrinfo(self, host: str, port: int, **_: Any) -> list[Any]:
        self.lookups.append(host)
        record = self.records.get(host)
        if record is None:
            raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided, or not known")
        if isinstance(record, socket.gaierror):
            raise record
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (record, port))]


# example.com's address: anywhere on the public internet.
PUBLIC_ADDRESS = "93.184.215.14"


@pytest.fixture
def dns(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> FakeDns:
    """Name lookups as a real job sees them, with private addresses refused. The test shop
    runs on this machine, so a test that needs it to pass the check points it at
    PUBLIC_ADDRESS."""
    settings.FETCH_PRIVATE_ADDRESSES = False
    fake = FakeDns()
    monkeypatch.setattr("jobs.page.socket", fake)
    return fake


# --- Scene steps ------------------------------------------------------------------------------


class HeldSteps:
    """Scene steps started in the background, held until the test runs them."""

    def __init__(self) -> None:
        self.held: list[int] = []

    def run_held(self) -> None:
        """Run every step held so far, oldest first, as a worker would."""
        while self.held:
            self.run_next()

    def run_next(self) -> None:
        """Run the oldest step held."""
        tasks.run_scene_step(self.held.pop(0))


class WorkerStopped(BaseException):
    """The worker running a step stopped mid-way, as when its machine is shut down."""


@pytest.fixture
def steps(monkeypatch: pytest.MonkeyPatch) -> Iterator[HeldSteps]:
    held = HeldSteps()
    monkeypatch.setattr(tasks.run_scene_step, "delay", held.held.append)
    yield held


@pytest.fixture
def checked(fake_model: FakeModel, planned: None, say: Callable[..., None]) -> None:
    """A chat whose ad is planned, has its person, and whose three lines passed the fact
    check."""
    fake_model.respond(
        "produce",
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says="The script is checked."),
    )
    fake_model.respond("fact_check", FACTS_OK)
    say("Make the person and check the script")


# The finishing-step rule (Julian 10 Oct 02:59), pinned word for word for the planner and
# the rewrite alike.
FINISHING_STEP = (
    "A finishing step that isn't the product doing its job, such as rinsing, flushing, drying "
    "or putting it away, never gets a B-roll scene of its own: the voice says it over the main "
    "step's scene, or the person says it to camera."
)
