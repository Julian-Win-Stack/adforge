"""The owner-notes run (evals/owner_notes): the shop owner's side of the chat, what a case
keeps, and how round 4's saved ads are read and replayed."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from adforge.file_store import read
from chat.models import Message, Session
from evals.owner_notes.chat import SAY_YES, Chat, Notes, send_notes, since
from evals.owner_notes.provider import OwnerNotesProvider
from evals.owner_notes.record import record
from evals.owner_notes.saved_ads import SavedAd, SavedScene
from gateway.fake import COLOURS, FakeModel, turn
from gateway.gateway import use_model
from jobs.models import Job, ProducedItem

from .conftest import HeldSteps, colour_at, picture
from .fake_fixing_agent import FakeAgent, ScriptedTry, install
from .test_producer_broll import (
    assemble_through_the_chat,
    checked,  # noqa: F401 (a fixture)
)
from .test_producer_picture_then_clip import assembled  # noqa: F401 (a fixture)

pytestmark = pytest.mark.django_db(transaction=True)

NOTE_2 = "Scene 2 just sits there. The hand should turn the mug all the way round."
TURN = "The hand turns the mug all the way round."


def a_chat() -> Chat:
    return Chat(str(Session.objects.get().pk))


def test_a_case_sends_the_note_to_the_producer_and_keeps_what_was_made(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    assembled: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat = a_chat()
    start = since(chat)
    # The producer hands the note to the fixing agent (door 2), scripted to pass its first try.
    fake_model.respond(
        "produce",
        turn(calls=[("fix_scene", {"scene": 2, "instruction": TURN})]),
        turn(says="I'm fixing scene 2."),
        turn(says="Scene 2 is fixed."),
    )
    agent = install(monkeypatch, FakeAgent(tries=[ScriptedTry()]))

    turned = send_notes(chat, Notes([NOTE_2]))
    kept = record(chat, start, turned, {"id": "mug-turn", "ad": "mug", "wants": "a turn"}, tmp_path)

    assert turned.sent == [NOTE_2]
    assert turned.error == ""
    assert [(call["tool"], call["arguments"]) for call in kept["tool_calls"]][0] == (
        "fix_scene",
        {"scene": 2, "instruction": TURN},
    )
    [handed] = agent.handed
    assert (handed["door"], handed["goal"], handed["failure"].note) == ("owner", TURN, NOTE_2)
    assert "plans" not in kept
    assert json.loads((tmp_path / "record.json").read_text())["sent"] == turned.sent
    text = (tmp_path / "chat.md").read_text()
    assert f"**OWNER:** {NOTE_2}" in text
    assert "Fix plans" not in text


def test_a_scripted_yes_is_typed_as_the_shop_owners_message(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    assembled: ProducedItem,  # noqa: F811
    steps: HeldSteps,
) -> None:
    chat = a_chat()
    fake_model.respond("produce", turn(says="Great."))

    turned = send_notes(chat, Notes([], turns=["yes"]))

    assert turned.sent == [SAY_YES]
    assert Message.objects.filter(role="user").last().text == SAY_YES  # type: ignore[union-attr]


def test_a_crash_is_kept_as_the_cases_error(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    assembled: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat = a_chat()

    def broken(*_: object) -> None:
        raise RuntimeError("the producer fell over")

    monkeypatch.setattr(chat, "say", broken)
    turned = send_notes(chat, Notes([NOTE_2]))

    assert turned.error == "RuntimeError: the producer fell over"
    assert Message.objects.filter(text=NOTE_2).count() == 0


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


def a_round_4_of(job: Job, folder: Path) -> SavedAd:
    """The planned job's ad as round 4 would have saved it: a picture of its own for each
    scene, and a yellow clip, the colour the fake makes last."""
    maker = FakeModel()
    made = [
        maker.submit_broll(starting_picture=None, example_pictures=[], seconds=6, prompt="r4")
        for _ in COLOURS
    ]
    yellow = maker.download(url=maker.status(video_id=made[-1]).video_url or "")
    scenes = []
    for scene in job.scenes.order_by("number"):
        saved = folder / f"scene-{scene.number}"
        saved.mkdir(parents=True)
        (saved / "start-picture.png").write_bytes(picture(720, 1280, (scene.number * 60, 9, 9)))
        (saved / "clip.mp4").write_bytes(yellow)
        scenes.append(SavedScene(scene.number, scene.line, shows=scene.shows, folder=saved))
    return SavedAd("mug", folder, "mug", "https://shop.test/mug", [], scenes)


def test_the_first_version_is_made_from_round_4s_pictures_and_clips(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    tmp_path: Path,
) -> None:
    ad = a_round_4_of(Job.objects.get(), tmp_path / "round-4")

    with use_model(OwnerNotesProvider(ad, real=fake_model)):
        assemble_through_the_chat(fake_model, steps, say)

    made = ProducedItem.objects.filter(scene__number=2)
    # Spec #210 point 19: scene 2 shows the product, so no starting picture is drawn for it;
    # its clip is still round 4's own.
    assert not made.filter(kind=ProducedItem.Kind.STARTING_PICTURE).exists()
    # The fake's own clips for the talking scenes are red and lime.
    assert colour_at(read(made.get(kind=ProducedItem.Kind.CLIP).file), 1.0) == "yellow"
