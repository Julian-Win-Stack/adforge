"""The look (spec #247 point 2, ticket #252), driven through the chat: the producer asks its
look about a scene's clip, a kept version, a library item or a try of a fix; the look is a
sub-call on the producer's own model, shown the pictures, the frames and the sent list with
the producer's question, and it answers in words only, in the judge's fault shape. No picture
enters the producer's conversation. Every model is faked at the gateway."""

import io
from collections.abc import Callable
from typing import Any

import PIL.Image
import pytest

from adforge import file_store
from agents import look
from agents.models import ToolCall
from chat.models import Session
from gateway import catalog
from gateway.fake import FakeModel, turn
from gateway.gateway import last_turn_given
from gateway.models import ModelCall
from jobs.library import OwnerPart, PictureLabel, add_to_library
from jobs.models import AgentFix, AgentFixTry, Job, ProducedItem, Scene, SceneStep

from .conftest import given_to_the_producer
from .sent_lists import holding_the_fixture

pytestmark = pytest.mark.django_db(transaction=True)

WORDS = {
    "sees": "The press stays closed from 0 s to 2 s.",
    "wrong": "The press never opens, from 0 s; a real one opens before it is squeezed.",
    "root_cause": "Image 3 shows the press closed, and the prompt never says it opens.",
    "direction": "Send a picture of the press open, and say in the prompt that it opens first.",
}


def _png(colour: tuple[int, int, int]) -> bytes:
    made = io.BytesIO()
    PIL.Image.new("RGB", (30, 40), colour).save(made, format="PNG")
    return made.getvalue()


def _saved(name: str, colour: tuple[int, int, int]) -> str:
    return file_store.save(name, _png(colour))


def _step(scene: Scene, kind: str, call: ToolCall, **fields: Any) -> SceneStep:
    return SceneStep.objects.create(
        scene=scene,
        kind=kind,
        status=SceneStep.Status.FINISHED,
        tool_call=call,
        line=scene.line,
        shows=scene.shows,
        **fields,
    )


def _version(scene: Scene, call: ToolCall, number: int, note: str = "") -> ProducedItem:
    """A starting picture made for `note` and its clip, judged as passed with two kept
    frames. Gives the clip."""
    picture_step = _step(scene, SceneStep.Kind.STARTING_PICTURE, call, note=note)
    picture = ProducedItem.objects.create(
        job=scene.job,
        scene=scene,
        step=picture_step,
        kind=ProducedItem.Kind.STARTING_PICTURE,
        version=number,
        file=_saved(f"s{scene.number}_v{number}.png", (10 * number, 20, 30)),
        change=note,
    )
    clip_step = _step(
        scene,
        SceneStep.Kind.CLIP,
        call,
        picture=picture,
        picture_step=picture_step,
        motion_prompt=f"A hand squeezes the press, take {number}.",
    )
    frames = [
        {"times": [0.0], "key": _saved(f"s{scene.number}_v{number}_f0.png", (1, 2, 3))},
        {"times": [0.2], "key": _saved(f"s{scene.number}_v{number}_f1.png", (4, 5, 6))},
    ]
    return ProducedItem.objects.create(
        job=scene.job,
        scene=scene,
        step=clip_step,
        kind=ProducedItem.Kind.CLIP,
        version=number,
        file=f"s{scene.number}_v{number}.mp4",
        picture=picture,
        ruling={"verdict": "pass", "checks": {}, "pictures": {"frames": frames, "sides": []}},
        change=note,
    )


@pytest.fixture
def ad(session_id: str) -> Job:
    """The chat's ad, with clips: scene 1 talks to camera; scene 2 shows the product, made
    first, then again for a red press."""
    session = Session.objects.get(pk=session_id)
    job = Job.objects.create(session=session, product_url="https://shop.example/products/press")
    call = ToolCall.objects.create(
        session=session, job=job, agent="producer", tool="make_clip", call_id="c0", arguments={}
    )
    talking = Scene.objects.create(job=job, number=1, line="Meet the press.", fact_checked=True)
    _version(talking, call, 1)
    pressing = Scene.objects.create(
        job=job,
        number=2,
        line="One squeeze and it's done.",
        shows="A hand squeezes a clove in the press.",
        broll_kind=Scene.BrollKind.DOES_A_JOB,
        person_shown=Scene.PersonShown.NO_FACE,
        fact_checked=True,
    )
    _version(pressing, call, 1)
    _version(pressing, call, 2, note="red press")
    return job


@pytest.fixture
def owner_asks(fake_model: FakeModel, say: Callable[..., None]) -> Callable[..., None]:
    """The shop owner's message once the ad has a clip, answered by the producer with
    `turns`. Until the old path goes (#262) code routes it through the decider first, which
    sorts it as no fix note."""

    def asking(text: str, *turns: Any) -> None:
        fake_model.respond("decide_fixes", turn(calls=[("sort_message", {"kind": "not_a_fix"})]))
        fake_model.respond("produce", *turns)
        say(text)

    return asking


def _look_call() -> ModelCall:
    [call] = ModelCall.objects.filter(purpose=look.PURPOSE)
    return call


def _labels(call: ModelCall) -> list[str]:
    return [image["label"] for image in call.images]


def test_a_look_at_a_clip_is_shown_its_sent_list_and_frames_and_answers_in_words(
    fake_model: FakeModel, ad: Job, owner_asks: Callable[..., None]
) -> None:
    clip = ad.scenes.get(number=2).produced.filter(kind=ProducedItem.Kind.CLIP).latest("id")
    sent = holding_the_fixture(clip)
    question = "Does the press open before it is squeezed?"
    fake_model.respond(look.PURPOSE, WORDS)

    owner_asks(
        "Is the press opening in scene 2?",
        turn(calls=[("look", {"scene": 2, "what": "clip", "number": None, "question": question})]),
        turn(says="It never opens."),
    )

    looked = _look_call()
    # On the producer's own model, charged to the producer's tool call, with its why.
    assert looked.model == catalog.MODEL_FOR_PURPOSE["produce"]
    [row] = ToolCall.objects.filter(agent="producer", tool="look")
    assert looked.tool_call_id == row.pk
    assert row.why and row.answer == "made"
    # Shown what the clip was sent, in order, then the clip's frames.
    named = "Scene 2, v2 red press (the current version)"
    labels = _labels(looked)
    assert labels[:9] == [f"{named}, Image {each.image}: {each.label}" for each in sent.pictures]
    assert labels[-2:] == [
        "Scene 2, clip of v2 red press (the current version), Frame at 0 s",
        "Scene 2, clip of v2 red press (the current version), Frame at 0.2 s",
    ]
    # Handed the sent list in words, its prompt, the scene and the producer's question.
    handed = " ".join(str(value) for value in looked.handoff.values())
    assert f"The prompt it was sent: {sent.prompt}" in looked.handoff["shown"]
    assert "Left out of the clip: The garlic press, closed, seen from the top." in handed
    assert "A hand squeezes a clove in the press." in handed
    assert question in handed
    # The producer reads words only: the fault shape, and no picture.
    assert row.pictures == []
    for said in WORDS.values():
        assert said in row.result
    given = given_to_the_producer(2)[-1]
    assert given["tool"] == "look" and "pictures" not in given
    # The producer's last turn is its own, never the look: it was given the look's words.
    last = last_turn_given(row.session, "produce")
    assert last is not None and last[-1].result == row.result  # type: ignore[union-attr]


def _asks(what: str, number: int | None, scene: int | None = 2) -> dict[str, Any]:
    return {"scene": scene, "what": what, "number": number, "question": "What is wrong?"}


def test_a_look_at_an_earlier_version_made_from_a_starting_picture_shows_it_then_its_clip(
    fake_model: FakeModel, ad: Job, owner_asks: Callable[..., None]
) -> None:
    fake_model.respond(look.PURPOSE, WORDS)

    owner_asks(
        "Was the first scene 2 better?",
        turn(calls=[("look", _asks("version", 1))]),
        turn(says="The first one was closed too."),
    )

    looked = _look_call()
    first = ad.scenes.get(number=2).produced.get(kind=ProducedItem.Kind.STARTING_PICTURE, version=1)
    assert looked.images[0] == {
        "label": "Scene 2, v1 first version, starting picture",
        "key": first.file,
    }
    assert _labels(looked)[1:] == [
        "Scene 2, clip of v1 first version, Frame at 0 s",
        "Scene 2, clip of v1 first version, Frame at 0.2 s",
    ]
    handed = " ".join(str(value) for value in looked.handoff.values())
    assert "A hand squeezes the press, take 1." in handed
    assert "The judge's verdict: pass." in handed
    [row] = ToolCall.objects.filter(tool="look")
    assert row.result.startswith("The look at scene 2, v1 first version, asked: What is wrong?")


def test_a_look_at_what_isnt_there_is_a_cant_and_nothing_is_paid(
    fake_model: FakeModel, ad: Job, owner_asks: Callable[..., None]
) -> None:
    owner_asks(
        "Look at scene 2.",
        turn(calls=[("look", _asks("version", 3))]),
        turn(calls=[("look", _asks("library_item", 7, scene=None))]),
        turn(calls=[("look", _asks("try", 1))]),
        turn(says="There is nothing there."),
    )

    results = list(ToolCall.objects.filter(tool="look").order_by("id"))
    assert [row.answer for row in results] == ["cant", "cant", "cant"]
    assert results[0].result.startswith("Refused: scene 2 has versions 1 to 2, not 3.")
    assert results[1].result.startswith("Refused: there is no library item 7.")
    assert results[2].result.startswith("Refused: scene 2 has had no fix, so it has no tries.")
    assert not ModelCall.objects.filter(purpose=look.PURPOSE).exists()


def test_a_look_at_a_library_item_alone_is_shown_its_picture(
    fake_model: FakeModel, ad: Job, owner_asks: Callable[..., None]
) -> None:
    item = add_to_library(
        ad,
        _saved("lid.png", (200, 0, 0)),
        PictureLabel(kind="side", side="top", alone="yes", colour="red", label="The press's lid."),
        OwnerPart(part="lid"),
    )
    fake_model.respond(look.PURPOSE, WORDS)

    owner_asks(
        "Is the lid red?",
        turn(calls=[("look", _asks("library_item", item.number, scene=None))]),
        turn(says="It is red."),
    )

    looked = _look_call()
    assert looked.images == [
        {"label": f"Library item {item.number}: The press's lid.", "key": item.file}
    ]
    assert looked.handoff["scene"] == ""
    assert "Colour red." in looked.handoff["shown"]
    [row] = ToolCall.objects.filter(tool="look")
    assert row.answer == "made" and WORDS["direction"] in row.result


def test_a_look_at_a_try_of_the_scenes_fix_is_shown_what_it_was_sent_and_its_frames(
    fake_model: FakeModel, ad: Job, owner_asks: Callable[..., None]
) -> None:
    scene = ad.scenes.get(number=2)
    failed = scene.produced.filter(kind=ProducedItem.Kind.CLIP).latest("id")
    assert failed.step is not None
    fixing = AgentFix.objects.create(
        step=failed.step, door=AgentFix.Door.JUDGE, first_clip=failed, ruling={"verdict": "fail"}
    )
    tried = _version(scene, failed.step.tool_call, 3, note="press opens")
    AgentFixTry.objects.create(
        agent_fix=fixing,
        number=1,
        tool_call=failed.step.tool_call,
        step=tried.step,
        picture=tried.picture,
        clip=tried,
    )
    sent = holding_the_fixture(tried)
    fake_model.respond(look.PURPOSE, WORDS)

    owner_asks(
        "Did the fix's first try open the press?",
        turn(calls=[("look", _asks("try", 1))]),
        turn(says="It did not."),
    )

    looked = _look_call()
    labels = _labels(looked)
    assert labels[:9] == [
        f"Scene 2, try 1, Image {each.image}: {each.label}" for each in sent.pictures
    ]
    assert labels[-1] == "Scene 2, try 1, clip, Frames at 0 s, 0.2 s, left to right"
    assert sent.prompt in looked.handoff["shown"]
    [row] = ToolCall.objects.filter(tool="look")
    assert row.result.startswith("The look at scene 2, try 1 of its newest fix, asked:")


def test_past_the_most_pictures_the_references_stay_whole_and_the_frames_are_joined(
    fake_model: FakeModel, ad: Job, owner_asks: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(look, "MOST_PICTURES", 12)
    clip = ad.scenes.get(number=2).produced.filter(kind=ProducedItem.Kind.CLIP).latest("id")
    sent = holding_the_fixture(clip)
    fake_model.respond(look.PURPOSE, WORDS)

    owner_asks("Look at scene 2.", turn(calls=[("look", _asks("clip", None))]), turn(says="Done."))

    named = "Scene 2, v2 red press (the current version)"
    labels = _labels(_look_call())
    assert labels[:9] == [f"{named}, Image {each.image}: {each.label}" for each in sent.pictures]
    video_1, video_2, frames = labels[9:]
    assert video_1.startswith(f"{named}, Video 1, Frames at 0 s, 0.2 s, ")
    assert video_2.startswith(f"{named}, Video 2, Frames at 0 s, 0.2 s, ")
    assert frames == (
        "Scene 2, clip of v2 red press (the current version), Frames at 0 s, 0.2 s, left to right"
    )


@pytest.mark.parametrize(
    ("asked", "refused"),
    [
        ({**_asks("clip", None), "question": " "}, "Refused: ask the look one question"),
        (_asks("clip", 1), "Refused: the clip, the person and the place take no number"),
        (_asks("version", None), "Refused: the clip, the person and the place take no number"),
        (_asks("clip", None, scene=None), "Refused: name the scene to look at."),
        (_asks("clip", None, scene=9), "Refused: the ad has no scene 9."),
    ],
)
def test_a_look_asked_wrongly_is_a_cant_saying_why_and_nothing_is_paid(
    fake_model: FakeModel,
    ad: Job,
    owner_asks: Callable[..., None],
    asked: dict[str, Any],
    refused: str,
) -> None:
    owner_asks("Look at scene 2.", turn(calls=[("look", asked)]), turn(says="Sorry."))

    [row] = ToolCall.objects.filter(tool="look")
    assert row.answer == "cant" and row.result.startswith(refused)
    assert not ModelCall.objects.filter(purpose=look.PURPOSE).exists()


def test_a_try_with_no_sent_list_is_shown_its_frames_and_told_its_video_prompt(
    fake_model: FakeModel, ad: Job, owner_asks: Callable[..., None]
) -> None:
    failed = ad.scenes.get(number=2).produced.filter(kind=ProducedItem.Kind.CLIP).latest("id")
    assert failed.step is not None
    AgentFix.objects.create(
        step=failed.step, door=AgentFix.Door.JUDGE, first_clip=failed, ruling={"verdict": "fail"}
    )
    fake_model.respond(look.PURPOSE, WORDS)

    owner_asks(
        "What failed?", turn(calls=[("look", _asks("try", 0))]), turn(says="It stayed shut.")
    )

    looked = _look_call()
    # No start picture is shown or said (spec #210 point 19): its clip's frames only.
    assert _labels(looked)[0].startswith("Scene 2, try 0, clip, Frames at")
    assert "start picture" not in looked.handoff["shown"]
    assert "The clip's video prompt: A hand squeezes the press, take 2." in looked.handoff["shown"]
    assert "The judge's verdict: fail." in looked.handoff["shown"]


def test_the_same_look_asked_again_is_answered_once_and_paid_once(
    fake_model: FakeModel, ad: Job, owner_asks: Callable[..., None]
) -> None:
    fake_model.respond(look.PURPOSE, WORDS)

    owner_asks(
        "Look twice.",
        turn(calls=[("look", _asks("version", 1))]),
        turn(calls=[("look", _asks("version", 1))]),
        turn(says="Same answer."),
    )

    first, again = ToolCall.objects.filter(tool="look").order_by("id")
    assert first.result == again.result and WORDS["root_cause"] in again.result
    assert ModelCall.objects.filter(purpose=look.PURPOSE).count() == 1
