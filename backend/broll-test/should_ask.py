"""The "Should we ask?" eval (item 49 of docs/broll-picture-logic.md, ticket #100): does the
planner ask the shop owner because of B-roll exactly when it should? 8 written situations,
each with the right answer, run against the planner's real instructions. Text and photos
only: no pictures or videos are made. Not part of the test suite: it calls the real model.

Each case is a test product's page (docs/test-products.md) with some of its photos and,
for some, a line cut from its text or a message from the shop owner. The pages and photos
are saved in broll-test/should-ask/, so the eval needs no database of old runs:

    docker compose exec backend python broll-test/should_ask.py collect   # Firecrawl, once
    docker compose exec backend python broll-test/should_ask.py run       # 8 cases x 3 runs

Scoring: an "ask" case passes when the planner asks about that case's B-roll gap (its
question names one of the case's gap words); a "don't ask" case passes when it plans. A
question about something else, such as the price, counts neither way, and is listed for a
person to look at, as is an "ask" case's question that names none of its gap words. Writes
MEDIA_ROOT/broll-test/should-ask/results.json."""

import json
import mimetypes
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")

import django  # noqa: E402

django.setup()

from django.db import connection  # noqa: E402

from adforge import file_store  # noqa: E402
from gateway.gateway import call_model  # noqa: E402
from gateway.types import Image  # noqa: E402
from jobs import firecrawl, page  # noqa: E402
from jobs.planning import (  # noqa: E402
    PLAN_INSTRUCTIONS,
    ChatMessage,
    PlanHandoff,
    producer_decision_for,
)

HERE = Path(__file__).resolve().parent / "should-ask"
# Copied to docs/runs/broll-tests/ once a person has judged its "look" runs.
RESULTS = Path("/app/media/broll-test/should-ask/results.json")
RUNS = 3


@dataclass(frozen=True)
class Case:
    name: str
    product: str  # The page's folder in should-ask/, named after docs/test-products.md.
    url: str
    right: Literal["ask", "don't ask"]
    why: str
    # The page's photos sent, by their number in the page's folder, in order.
    photos: list[int]
    # Words a question about this case's B-roll gap names; empty for "don't ask".
    gap_words: list[str] = field(default_factory=list)
    # Lines of the page text left out: each line containing one of these is dropped.
    cut: list[str] = field(default_factory=list)
    # What is still to be chosen once the page is read; the eval won't run until it's "".
    to_fill: str = ""
    # What the shop owner said, after asking for the ad.
    said: list[str] = field(default_factory=list)


CASES = [
    Case(
        name="1 needed photo missing",
        product="08-mollys-suds",
        url="https://mollyssuds.com/products/toilet-bowl-cleaner",
        right="ask",
        why="The shop owner wants the gel squeezed under the rim; no photo shows the gel.",
        photos=[1, 2],
        gap_words=["gel", "liquid", "squeez", "out of the bottle", "inside"],
        said=["Show the cleaner being squeezed under the rim."],
    ),
    Case(
        name="2 does a job, no how to use",
        product="07-great-jones-dutch-baby",
        url="https://greatjonesgoods.com/products/dutch-baby",
        right="ask",
        why="A pan does a job you can see, and its page has had every line on how it's used cut.",
        photos=[1, 2, 3],
        gap_words=["how it's used", "how to use", "how you use", "used", "cook", "bake", "roast"],
        to_fill="cut: every line saying how it's used",
    ),
    Case(
        name="3 showcase, no how to use",
        product="05-steve-madden-bag",
        url="https://www.stevemadden.com/products/bkenzo-gold",
        right="don't ask",
        why='A bag is shown at its best: it needs no "how to use".',
        photos=[1, 2, 3, 4],
    ),
    Case(
        name="4 everything present",
        product="09-momofuku-chili-crunch",
        url="https://shop.momofuku.com/products/chili-crunch-sauce",
        right="don't ask",
        why="The page says what to put it on, and a photo shows it out of the jar.",
        photos=[1, 2, 3],
    ),
    Case(
        name="5 serum, one photo, texture",
        product="01-naturium-serum",
        url="https://naturium.com/products/multi-active-exosome-serum",
        right="ask",
        why="The shop owner wants a drop on a fingertip; the one photo shows only the bottle.",
        photos=[1],
        gap_words=["texture", "drop", "out of the bottle", "serum itself", "looks like"],
        said=["Show a drop of it on a fingertip."],
    ),
    Case(
        name="6 blush, cheek photo",
        product="02-merit-flush-balm",
        url="https://www.meritbeauty.com/products/flush-balm",
        right="don't ask",
        why="A photo shows the blush on a cheek, which is the needed photo.",
        photos=[1, 2, 3],
    ),
    Case(
        name="7 power bank charging a phone",
        product="06-anker-power-bank",
        url="https://www.anker.com/products/a1229",
        right="don't ask",
        why="The page says it charges phones; a phone is an ordinary thing, never missing.",
        photos=[1],
        to_fill="photos: only the power bank on its own",
    ),
    Case(
        name="8 no photo clearly shows the product",
        product="06-anker-power-bank",
        url="https://www.anker.com/products/a1229",
        right="ask",
        why="The only photos sent show the power bank among other products or hidden.",
        photos=[],
        to_fill="photos: only ones where it isn't clearly seen",
        gap_words=["photo", "picture", "clearly"],
    ),
]


def collect() -> None:
    """Read each case's page through Firecrawl, as the app does, and keep its visible text
    and every photo it declares, numbered from 1. A page already kept is left alone."""
    for url, product in {case.url: case.product for case in CASES}.items():
        folder = HERE / product
        if (folder / "page.txt").exists():
            continue
        folder.mkdir(parents=True, exist_ok=True)
        read = page.parse(firecrawl.as_download(firecrawl.read_page(url), url))
        (folder / "page.txt").write_text(read.text)
        for number, photo_url in enumerate(read.photo_urls, start=1):
            got = page.download(photo_url, max_bytes=page.MAX_PHOTO_BYTES, what="photo")
            extension = mimetypes.guess_extension(got.content_type) or ".jpg"
            (folder / f"photo-{number}{extension}").write_bytes(got.content)
        print(f"{product}: {len(read.photo_urls)} photos")


def photo_file(case: Case, number: int) -> Path:
    (found,) = (HERE / case.product).glob(f"photo-{number}.*")
    return found


def page_text(case: Case) -> str:
    text = (HERE / case.product / "page.txt").read_text()
    lines = [line for line in text.splitlines() if not any(cut in line for cut in case.cut)]
    return "\n".join(lines)


def plan_once(case: Case, run: int) -> dict[str, Any]:
    try:
        keys = [
            file_store.save(f"broll-test/should-ask/{case.product}/{path.name}", path.read_bytes())
            for path in (photo_file(case, number) for number in case.photos)
        ]
        conversation = [ChatMessage(by="user", text=f"Make an ad for {case.url}")]
        conversation += [ChatMessage(by="user", text=text) for text in case.said]
        decision = call_model(
            job=None,
            purpose="plan_ad",
            instructions=PLAN_INSTRUCTIONS,
            handoff=PlanHandoff(
                product_url=case.url,
                page_text=page.for_model(page_text(case)),
                target_seconds=None,
                photo_count=len(keys),
                conversation=conversation,
            ),
            output=producer_decision_for(len(keys)),
            images=[Image(label=f"Photo {n}", key=key) for n, key in enumerate(keys, start=1)],
        )
        return {"case": case.name, "run": run, **verdict(case, decision.question)}
    except Exception as error:  # noqa: BLE001 - a failed run is a result too
        return {"case": case.name, "run": run, "verdict": "failed", "error": repr(error)}
    finally:
        connection.close()


def verdict(case: Case, question: str | None) -> dict[str, Any]:
    """Pass or fail, or "look" for a question a person must judge: about this case's B-roll
    gap (pass for "ask", fail for "don't ask"), or about something else, such as the price,
    which counts neither way."""
    if question is None:
        return {"verdict": "pass" if case.right == "don't ask" else "fail"}
    words = "|".join(re.escape(word) for word in case.gap_words)
    if case.right == "ask" and re.search(rf"\b(?:{words})", question.casefold()):
        return {"verdict": "pass", "question": question}
    return {"verdict": "look", "question": question}


def run() -> None:
    unfilled = [f"{case.name}: {case.to_fill}" for case in CASES if case.to_fill]
    if unfilled:
        sys.exit("Fill these in first:\n" + "\n".join(unfilled))
    runs = [(case, number) for case in CASES for number in range(1, RUNS + 1)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda each: plan_once(*each), runs))
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(results, indent=2))
    print("Wrote", RESULTS)
    for case in CASES:
        mine = [result for result in results if result["case"] == case.name]
        # A question about something else counts neither way, so a "look" left as one is
        # out of the rate: a person judges each and edits its verdict in the results.
        counted = [result for result in mine if result["verdict"] in ("pass", "fail")]
        passed = sum(result["verdict"] == "pass" for result in counted)
        looks = len(mine) - len(counted)
        print(f"{case.name} ({case.right}): {passed}/{len(counted)} passed, {looks} to look at")
        for result in mine:
            if result["verdict"] != "pass":
                print(f"   {result['verdict']}: {result.get('question') or result.get('error')}")


if __name__ == "__main__":
    {"collect": collect, "run": run}[sys.argv[1]]()
