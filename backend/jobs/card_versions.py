"""A scene card's clip versions, for the shop owner to watch and swap one into the ad from the
card (clip version picker, Julian 2026-10-29 22:16): every clip the scene was shown, tries
included, in one numbering, each with the clip judge's verdict, whether it is the one in the
ad, and whether it can go in the ad for nothing. A swap is done here, by code, with nothing
made, judged or paid for: no producer step."""

from dataclasses import dataclass
from typing import Literal

from . import owner_answers, work
from .models import ProducedItem, Scene

# What the clip judge said of a clip, as the card badges it. A clip it never looked at (a
# talking scene's, or one made before it) is "not_checked".
Verdict = Literal["pass", "fail", "unsure", "not_checked"]


@dataclass(frozen=True)
class CardVersion:
    """One clip of a scene as its card shows it."""

    # The scene version it is, as the card, read_ad and use_version number it: 2 for v2.
    number: int
    label: str
    clip: ProducedItem
    verdict: Verdict
    # What the judge saw wrong, in its words, when it failed the clip; blank otherwise.
    problem: str
    in_ad: bool
    # Whether it can go in the ad for nothing: it still says the scene's line as it stands,
    # or it is a try on offer. Never true of the one in the ad.
    free: bool


class CantUse(Exception):
    """A swap from the card that can't be done, saying why in the shop owner's words."""


def shown(scene: Scene) -> list[CardVersion]:
    """The scene's clips as its card shows them, oldest first. A clip brought back by going
    back is the version it was brought back from, shown once: going back to v2 and back again
    leaves the card with the versions it had."""
    made = work.versions(scene)
    # Each version with a clip, as the version the card shows it as: a clip brought back is
    # the one it was brought back from.
    shown_as: dict[int, int] = {}
    for version in made:
        if version.clip is None:
            continue
        back = made[version.back_to - 1] if version.back_to else None
        copied = back is not None and back.clip is not None and back.clip.file == version.clip.file
        shown_as[version.number] = shown_as[back.number] if copied and back else version.number
    in_ad = _in_the_ad(scene, made, shown_as)
    found: list[CardVersion] = []
    for version in made:
        clip = version.clip
        if clip is None or shown_as[version.number] != version.number:
            continue
        here = version.number == in_ad
        verdict, problem = _judged(clip)
        found.append(
            CardVersion(
                number=version.number,
                label=version.label,
                clip=clip,
                verdict=verdict,
                problem=problem,
                in_ad=here,
                free=not here and _free(scene, version),
            )
        )
    return found


def use(scene: Scene, number: int) -> work.GoneBack:
    """Put the scene's version `number` in the ad from its card, for nothing, as the shop owner
    tapped: its clip is brought back as the scene's newest version and a question the fixing
    agent asked about the scene is withdrawn. The card shows it: the chat isn't shown the
    clip again under another version's number. Refuses, saying why, a
    version the card doesn't show, the one in the ad, one that no longer fits the scene, and
    while the scene's picture or clip is being made."""
    picked = next((each for each in shown(scene) if each.number == number), None)
    if picked is None:
        raise CantUse(f"Scene {scene.number} has no v{number} to use.")
    if picked.in_ad:
        raise CantUse(f"v{number} is already in the ad.")
    if not picked.free:
        raise CantUse(
            f"v{number} was made for an older line of scene {scene.number}, so it can't go in "
            "the ad for free."
        )
    try:
        if work.says_the_line_as_it_stands(scene, picked.clip):
            gone = work.go_back(scene, number, show=False)
        else:
            # A try of a scene said to camera since: swapping it in shows the product again.
            gone = work.use_try(picked.clip, show=False)
    except work.SceneBusy as busy:
        raise CantUse(
            f"Scene {scene.number}'s picture or clip is being made right now. Try again once "
            "it's ready."
        ) from busy
    except work.TryNotOnOffer as refused:
        raise CantUse(str(refused)) from refused
    # A newer version moves the scene past any question its fixing agent asked about it.
    owner_answers.withdraw(scene)
    return gone


def _in_the_ad(scene: Scene, made: list[work.Version], shown_as: dict[int, int]) -> int | None:
    """The version the card shows the ad's clip for the scene as: its newest clip, while that
    still says the scene's line as it stands. None when the line changed since, as the scene
    waits for a clip of its new line."""
    newest = next((version for version in reversed(made) if version.clip), None)
    if newest is None or newest.clip is None:
        return None
    if not work.says_the_line_as_it_stands(scene, newest.clip):
        return None
    return shown_as[newest.number]


def _free(scene: Scene, version: work.Version) -> bool:
    """Whether `version`, not the one in the ad, can go in the ad for nothing."""
    assert version.clip is not None, "the card shows clips"
    # As go_back_is_free, from the versions already read; a try is asked about only when its
    # clip doesn't say the line, as a scene said to camera before 2026-10-23.
    return work.says_the_line_as_it_stands(scene, version.clip) or work.is_a_try_on_offer(
        version.clip
    )


def _judged(clip: ProducedItem) -> tuple[Verdict, str]:
    """The clip judge's verdict on `clip` and, when it failed it, the first problem it saw."""
    ruling = clip.ruling or {}
    verdict = ruling.get("verdict")
    if verdict == "fail":
        return "fail", work.first_problem(ruling)
    if verdict in ("pass", "unsure"):
        return verdict, ""
    return "not_checked", ""
