"""The code tests: two checks done by counting over every finished ad, no model, free (Julian,
Nov 8). They catch a change that quietly drops something without him watching every ad.

- **Demo floor:** the ad has at least one demo scene: a B-roll scene, or a talking scene in the
  style "talking while doing it" (`jobs.plan_rules.DOING_IT`). Any other talking scene counts
  as talking, even one that shows the result. An ad planned before talking styles (ADR 0013)
  has none, so only its B-roll scenes count.
- **The ending:** the end card's product photo stays on screen at least 1.5 s at the end of the
  finished video, and the brand or the product name is said at least once anywhere in the ad,
  in what was heard (each clip's word check `said`), not only the planned line.

Each ad is read as shipped: its newest finished version, the scenes whose clips it plays, and
the video itself, measured with ffprobe. From backend/, where the ads were made (the Azure box
for a test round):

    uv run python -m evals.code_tests <chat session id> ...
    uv run python -m evals.code_tests --ads <ads.json>

ads.json is {"<ad key>": "<chat session id>", ...}, as `scripts/ad-runs/run.py` writes it: one
ad per session, its newest job. It prints one row per ad and how many pass each test, and exits
1 when any ad fails a test or has no finished ad.
"""

import argparse
import json
import os
import re
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from jobs.plan_rules import DOING_IT

if TYPE_CHECKING:
    from jobs.models import Job

END_CARD_AT_LEAST = 1.5


@dataclass(frozen=True)
class Scene:
    """One scene the finished ad plays: its kind ("talking" or "broll"), its talking style
    (blank for B-roll and for plans before talking styles) and the words heard in its clip
    (None when its clip has no word check)."""

    number: int
    kind: str
    style: str
    heard: str | None


@dataclass(frozen=True)
class Shipped:
    """A finished ad as the owner got it. `end_card_seconds` is how long the end card plays at
    the end of the video, None when the ad has no end card."""

    key: str
    scenes: list[Scene]
    brand: str
    product_name: str
    end_card_seconds: float | None
    end_card_photo: bool


@dataclass(frozen=True)
class Verdict:
    passed: bool
    why: str


def demo_floor(ad: Shipped) -> Verdict:
    demos = [
        f"{scene.number} ({'B-roll' if scene.kind == 'broll' else DOING_IT})"
        for scene in ad.scenes
        if scene.kind == "broll" or scene.style == DOING_IT
    ]
    if demos:
        return Verdict(True, f"demo scene {', '.join(demos)}")
    return Verdict(False, f'no B-roll scene and no talking scene in the style "{DOING_IT}"')


def end_card(ad: Shipped) -> Verdict:
    if ad.end_card_seconds is None:
        return Verdict(False, "no end card")
    held = f"{ad.end_card_seconds:.2f} s"
    if not ad.end_card_photo:
        return Verdict(False, f"end card {held} with no product photo")
    if ad.end_card_seconds < END_CARD_AT_LEAST:
        return Verdict(False, f"end card held {held}, under {END_CARD_AT_LEAST:g} s")
    return Verdict(True, f"end card held {held}")


def name_said(ad: Shipped) -> Verdict:
    names = [name for name in (ad.brand, ad.product_name) if squeezed(name)]
    if not names:
        return Verdict(False, "the plan has no brand or product name")
    unheard = [scene.number for scene in ad.scenes if scene.heard is None]
    for scene in ad.scenes:
        for name in names:
            if scene.heard is not None and squeezed(name) in squeezed(scene.heard):
                return Verdict(True, f'"{name}" said in scene {scene.number}')
    asked = " or ".join(f'"{name}"' for name in names)
    gap = f" (scene {', '.join(map(str, unheard))} has no word check)" if unheard else ""
    return Verdict(False, f"{asked} never heard{gap}")


def squeezed(text: str) -> str:
    """`text` with case, spaces and punctuation ignored and numbers as words (the word check's
    `tidy`), so "Hypervolt 2" is found in "the hypervolt two" and "Liquid I.V." in "liquid IV"."""
    from jobs.word_check import tidy

    return re.sub(r"\W", "", tidy(text))


def shipped(key: str, job: Job) -> Shipped | None:
    """`job`'s newest finished ad, or None when it has none."""
    from adforge import file_store
    from jobs import assembly
    from jobs.models import ProducedItem

    ad = job.produced.filter(kind=ProducedItem.Kind.FINISHED_AD).order_by("-version").first()
    if ad is None:
        return None
    clips = ProducedItem.objects.select_related("scene").in_bulk([cut["clip"] for cut in ad.cuts])
    scenes = []
    for cut in ad.cuts:
        if cut["end"] <= cut["start"]:
            continue  # said over the scene before: its picture never plays
        clip = clips[cut["clip"]]
        assert clip.scene is not None, "a finished ad's cut is a scene's clip"
        said = ((clip.ruling or {}).get("word_check") or {}).get("said")
        scenes.append(Scene(cut["scene"], clip.scene.kind, clip.scene.talking_style, said))
    seconds = None
    if ad.end_card:
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "ad.mp4"
            video.write_bytes(file_store.read(ad.file))
            seconds = assembly.seconds_of(video) - ad.end_card["start"]
    return Shipped(
        key=key,
        scenes=scenes,
        brand=job.brand,
        product_name=job.product_name,
        end_card_seconds=seconds,
        end_card_photo=bool((ad.end_card or {}).get("file")),
    )


def report(ads: dict[str, str]) -> tuple[str, bool]:
    """The table for `ads` ({key: chat session id}) and whether every ad passed both tests."""
    from jobs.models import Job

    rows = []
    totals = {"demo floor": 0, "end card": 0, "name said": 0, "the ending": 0}
    finished = 0
    for key, session in ads.items():
        job = Job.objects.filter(session_id=session).order_by("-created_at").first()
        ad = shipped(key, job) if job is not None else None
        if ad is None:
            rows.append(f"{key}: no finished ad")
            continue
        finished += 1
        demo, card, name = demo_floor(ad), end_card(ad), name_said(ad)
        ending = card.passed and name.passed
        for test, passed in (
            ("demo floor", demo.passed),
            ("end card", card.passed),
            ("name said", name.passed),
            ("the ending", ending),
        ):
            totals[test] += passed
        kinds = "".join("B" if scene.kind == "broll" else "T" for scene in ad.scenes)
        rows.append(
            f"{key} [{kinds}]: demo floor {_mark(demo)}; the ending "
            f"{'pass' if ending else 'FAIL'} ({_mark(card)}; {_mark(name)})"
        )
    lines = [*rows, "", f"{finished} of {len(ads)} ads finished"]
    lines += [f"{test}: {passed}/{finished} pass" for test, passed in totals.items()]
    return "\n".join(lines), finished == len(ads) and all(
        totals[test] == finished for test in ("demo floor", "the ending")
    )


def _mark(verdict: Verdict) -> str:
    return f"{'pass' if verdict.passed else 'FAIL'}: {verdict.why}"


def main(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.code_tests", description=__doc__)
    parser.add_argument("sessions", nargs="*", help="chat session ids, one ad each")
    parser.add_argument("--ads", type=Path, help='{"<ad key>": "<chat session id>", ...}')
    given = parser.parse_args(args)
    ads = {session: session for session in given.sessions}
    if given.ads is not None:
        ads |= json.loads(given.ads.read_text())
    if not ads:
        parser.error("give chat session ids or --ads")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adforge.settings")
    import django

    django.setup()
    table, passed = report(ads)
    print(table)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
