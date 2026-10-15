"""The owner-notes run (evals/owner_notes): the shop owner's side of the chat, what a case
keeps, and how round 4's saved ads are read and replayed."""

import json
from pathlib import Path
from typing import Any

import pytest

from chat.models import Message, Session
from evals.owner_notes.chat import GO_AHEAD, Chat, Notes, send_notes, since
from evals.owner_notes.provider import OwnerNotesProvider
from evals.owner_notes.record import record
from evals.owner_notes.saved_ads import as_planned, saved_ads
from gateway.fake import FakeModel, turn
from jobs.models import Fix, ProducedItem

from .conftest import HeldSteps
from .test_producer_broll import checked  # noqa: F401 (a fixture)
from .test_producer_fixes import FIX_2, NOTE_2, REWRITE, clip_with, restating
from .test_producer_fixes import shown as shown  # noqa: F401 (a fixture)

pytestmark = pytest.mark.django_db(transaction=True)


def a_chat() -> Chat:
    return Chat(str(Session.objects.get().pk))


def test_a_case_says_yes_to_the_restatement_and_keeps_what_was_made(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    tmp_path: Path,
) -> None:
    chat = a_chat()
    start = since(chat)
    fake_model.respond(
        "produce",
        turn(calls=[restating((2, FIX_2, "clip"))]),
        turn(says=f"I'll change scene 2: {FIX_2} Shall I?"),
        turn(calls=[clip_with(2, FIX_2)]),
        turn(says="Making scene 2's clip again."),
        *[turn(says="Here's the new ad.") for _ in range(3)],
    )
    fake_model.respond("rewrite_clip_prompt", REWRITE)

    turned = send_notes(chat, Notes([NOTE_2]))
    kept = record(chat, start, turned, {"id": "mug-pour", "ad": "mug", "wants": "a pour"}, tmp_path)

    assert turned.sent == [NOTE_2, GO_AHEAD]
    assert turned.error == ""
    assert kept["fixes"] == [
        {
            "scene": 2,
            "redo": "clip",
            "fix": FIX_2,
            "estimate_usd": kept["fixes"][0]["estimate_usd"],
            "said_yes": True,
        }
    ]
    clip = next(step for step in kept["steps"] if step["kind"] == "clip")
    assert clip["note"] == FIX_2
    assert clip["motion_prompt"] == REWRITE["motion_prompt"]
    assert clip["motion_prompt_before"] != clip["motion_prompt"]
    assert kept["new_ads_shown"] == 1
    assert json.loads((tmp_path / "record.json").read_text())["sent"] == turned.sent
    assert f"**OWNER:** {NOTE_2}" in (tmp_path / "chat.md").read_text()


def test_an_owner_who_says_no_has_nothing_made(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    tmp_path: Path,
) -> None:
    chat = a_chat()
    start = since(chat)
    fake_model.respond(
        "produce",
        turn(calls=[restating((2, FIX_2, "clip"))]),
        turn(says="That will cost about $0.80. Shall I?"),
        turn(says="No problem, I'll leave it as it is."),
    )

    turned = send_notes(chat, Notes([NOTE_2, "Never mind, leave it."], yes_answers=0))
    kept = record(chat, start, turned, {"id": "no", "ad": "mug"}, tmp_path)

    assert turned.sent == [NOTE_2, "Never mind, leave it."]
    assert [fix["said_yes"] for fix in kept["fixes"]] == [False]
    assert kept["steps"] == []
    assert kept["new_ads_shown"] == 0


def test_a_crash_is_kept_as_the_cases_error(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat = a_chat()

    def broken(_: str) -> None:
        raise RuntimeError("the producer fell over")

    monkeypatch.setattr(chat, "say", broken)
    turned = send_notes(chat, Notes([NOTE_2]))

    assert turned.error == "RuntimeError: the producer fell over"
    assert Message.objects.filter(text=NOTE_2).count() == 0
    assert Fix.objects.count() == 0


def saved_round_4(root: Path) -> Path:
    """A round 4 folder with one ad: a talking scene, then a B-roll scene with a need."""
    (root / "grades.md").write_text(
        "## #8 Toilet cleaner: Molly's Suds Cleaner (https://shop.test/cleaner)\n"
        "## N5 Le Duo: L'ange Le Duo (https://shop.test/le-duo)\n"
    )
    (root / "owner-answers.txt").write_text("#8 [msg4 stock?] -> It's in stock.\n")
    talking = root / "8-toilet-cleaner" / "scene-1-talking"
    broll = root / "8-toilet-cleaner" / "scene-2"
    for folder in (talking, broll):
        folder.mkdir(parents=True)
        (folder / "start-picture.png").write_bytes(folder.name.encode())
        (folder / "clip.mp4").write_bytes(b"clip of " + folder.name.encode())
    (talking / "inputs.txt").write_text(
        "PRODUCT: 8\nSCENE: 1\nLINE: Rings again?\nSHOWS: \nNEEDS: []\n"
    )
    (broll / "inputs.txt").write_text(
        "PRODUCT: 8\nSCENE: 2\nLINE: It fights rings.\nSHOWS: A brush scrubs the ring.\n"
        "BROLL_KIND: does a job\nPERSON_SHOWN: no face\nUSAGE: Scrub.\nRESULT: the ring gone\n"
        "NEEDS: [{'what': 'the gel', 'photos': [2, 7]}]\n\n[step 5 starting_picture finished] \n"
        "PICTURE PROMPT: A white toilet.\nPROMPT REASON: Shows the ring.\n"
        "MOTION PROMPT: The brush scrubs the ring once.\n"
    )
    return root


def test_round_4s_ads_are_read_from_their_folders(tmp_path: Path) -> None:
    ads = saved_ads(saved_round_4(tmp_path))

    ad = ads["8"]
    assert (ad.product_name, ad.url, ad.answers) == (
        "Molly's Suds Cleaner",
        "https://shop.test/cleaner",
        ["It's in stock."],
    )
    talking, broll = ad.scenes
    assert (talking.number, talking.line, talking.talking) == (1, "Rings again?", True)
    assert (broll.shows, broll.motion_prompt, broll.picture_prompt) == (
        "A brush scrubs the ring.",
        "The brush scrubs the ring once.",
        "A white toilet.",
    )
    assert broll.picture == b"scene-2"
    # Le Duo is graded, but has no folder here.
    assert list(ads) == ["8"]


def test_a_saved_scene_is_planned_with_only_the_photos_the_page_has_now(tmp_path: Path) -> None:
    broll = saved_ads(saved_round_4(tmp_path))["8"].scene(2)

    assert as_planned(broll, photo_count=5)["needs"] == [{"what": "the gel", "photos": [2]}]
    assert as_planned(broll, photo_count=1)["needs"] == []
    assert as_planned(saved_ads(tmp_path)["8"].scene(1), photo_count=5) == {"line": "Rings again?"}


def test_the_first_version_gets_round_4s_scenes_in_the_planners_plan(tmp_path: Path) -> None:
    ad = saved_ads(saved_round_4(tmp_path))["8"]
    provider = OwnerNotesProvider(ad, real=FakeModel())
    planned: dict[str, Any] = {
        "decision": "plan",
        "plan": {
            "product_name": "cleaner",
            "scenes": [
                {"line": "A", "part": "hook", "overlay": "New"},
                {"line": "B", "part": "proof", "overlay": None},
                {"line": "C", "part": "call to action", "overlay": "$9"},
            ],
        },
    }

    scenes = provider._round_4_plan(planned, photos=9)["plan"]["scenes"]

    assert [scene["line"] for scene in scenes] == ["Rings again?", "It fights rings."]
    # The last scene keeps the planner's last part, whatever came between.
    assert [(scene["part"], scene["overlay"]) for scene in scenes] == [
        ("hook", "New"),
        ("call to action", "$9"),
    ]
    assert scenes[1]["needs"] == [{"what": "the gel", "photos": [2, 7]}]
