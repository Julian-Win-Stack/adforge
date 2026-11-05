"""Starting pictures through the chat, with only external models scripted."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn
from jobs.models import ProducedItem

from .conftest import HeldSteps, handoffs, paid_for
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
PICTURE_FORM: dict[str, Any] = dict(
    everything_shown=[],
    camera="Phone at chest height",
    person_and_room="Hands at a desk",
    pose="One hand holds the mug",
    inside="not needed here",
    size="A quarter of the frame",
    moment="Pouring halfway through",
    light="Window on the left",
    bodies="Two hands",
    anything_else="not needed here",
    picks=[{"item": 1, "take": "Copy the shape and glaze."}],
)
PICTURE_PASSES = {
    "product_same": {"passes": True, "says": "The product matches."},
    "physics": {"passes": True, "says": "Everything is possible."},
}


@pytest.fixture
def picture_checked(fake_model: FakeModel) -> None:
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)


def test_drawn_picture_and_check_share_the_selected_inputs(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("give_up", {"reason": "Another view is needed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    made = ProducedItem.objects.get(kind="starting_picture", scene__number=2)
    assert made.sent is not None
    draw = handoffs("draw_scene_picture")[0]
    check = handoffs("check_scene_picture")[0]
    assert draw["pictures"] == [p["file"] for p in made.sent["photos"]]
    assert check["photos"] == [p["line"] for p in made.sent["photos"]]
    assert "Copy the shape and glaze." in draw["prompt"]
    assert made.ruling == {"status": "passed", "answers": PICTURE_PASSES}
    assert paid_for().count("draw_scene_picture") == 1
    assert ("draw_scene_picture", "1152x2048") in fake_model.edits_asked
    assert set(check) == {"product_name", "product_colour", "page_text", "owner_chat", "photos"}


def test_failed_picture_is_redrawn_with_answers_in_the_same_scene_chat(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    failure = {**PICTURE_PASSES, "product_same": {"passes": False, "says": "The rim is square."}}
    fake_model.respond("check_scene_picture", failure, PICTURE_PASSES)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("draw_starting_picture", {**PICTURE_FORM, "pose": "Round rim visible"})]),
        turn(calls=[("give_up", {"reason": "Need another angle."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    requests = handoffs("scene_agent")
    assert "The rim is square." in str(requests[1])
    assert "The rim is square." in str(requests[2])
    assert paid_for().count("draw_scene_picture") == 2
    assert list(
        ProducedItem.objects.filter(kind="starting_picture", scene__number=2)
        .order_by("version")
        .values_list("ruling__status", flat=True)
    ) == ["failed", "passed"]


def test_unavailable_check_keeps_picture_unchecked_and_tells_owner(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: Any,
    session_id: str,
) -> None:
    from gateway.types import UnusableReply

    from .conftest import recorded

    fake_model.respond(
        "check_scene_picture", UnusableReply("No answer", input_tokens=0, output_tokens=0)
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("give_up", {"reason": "Need another angle."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    made = ProducedItem.objects.get(kind="starting_picture", scene__number=2)
    assert made.ruling == {"status": "unchecked", "answers": None}
    assert any("look at it yourself" in text for _, text in recorded(api, session_id))


def test_failed_picture_can_be_edited_then_checked_with_same_picks(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    failure = {**PICTURE_PASSES, "product_same": {"passes": False, "says": "The rim is square."}}
    fake_model.respond("check_scene_picture", failure, PICTURE_PASSES)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(
            calls=[
                (
                    "edit_starting_picture",
                    {
                        "keep": ["the room", "the hand"],
                        "change": "Make the rim round",
                        "anything_else": "not needed here",
                        "picks": PICTURE_FORM["picks"],
                    },
                )
            ]
        ),
        turn(calls=[("give_up", {"reason": "Need a different angle."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show use.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    pictures = list(
        ProducedItem.objects.filter(kind="starting_picture", scene__number=2).order_by("version")
    )
    assert len(pictures) == 2
    draws = handoffs("draw_scene_picture")
    assert draws[1]["pictures"][0] == pictures[0].file
    assert pictures[1].sent is not None
    assert draws[1]["pictures"][1:] == [p["file"] for p in pictures[1].sent["photos"]]
    assert "Edit image 1. Keep the room; the hand exactly the same." in draws[1]["prompt"]
    assert pictures[1].ruling == {"status": "passed", "answers": PICTURE_PASSES}
    assert paid_for().count("draw_scene_picture") == 2


@pytest.mark.parametrize("edits", [1, 2])
def test_edited_picture_keeps_earlier_photos_for_its_check_and_clip_judge(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
    edits: int,
) -> None:
    from gateway.models import ModelCall

    from .test_scene_film import FILM

    fake_model.respond(
        "scene_agent",
        turn(
            calls=[
                (
                    "draw_starting_picture",
                    {
                        **PICTURE_FORM,
                        "picks": [
                            {"item": 1, "take": "Copy the shape."},
                            {"item": 2, "take": "Copy the handle."},
                        ],
                    },
                )
            ]
        ),
        turn(
            calls=[
                (
                    "edit_starting_picture",
                    {
                        "keep": ["The mug, room and hand"],
                        "change": "Show the handle more clearly",
                        "anything_else": "not needed here",
                        "picks": [{"item": 2, "take": "Copy the handle's curve."}],
                    },
                )
            ]
        ),
        *(
            [
                turn(
                    calls=[
                        (
                            "edit_starting_picture",
                            {
                                "keep": ["The mug, room and hand"],
                                "change": "Turn the mug towards the camera",
                                "anything_else": "not needed here",
                                "picks": [{"item": 1, "take": "Copy the round rim."}],
                            },
                        )
                    ]
                )
            ]
            if edits == 2
            else []
        ),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "The clip should pass."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    pictures = list(
        ProducedItem.objects.filter(kind="starting_picture", scene__number=2).order_by("version")
    )
    original, edited = pictures[0], pictures[-1]
    assert original.sent is not None and edited.sent is not None
    a, b, shared = [p["file"] for p in original.sent["photos"]]
    assert handoffs("draw_scene_picture")[1]["pictures"] == [original.file, b]
    assert [p["library_number"] for p in edited.sent["photos"]] == [2 if edits == 1 else 1]
    if edits == 2:
        assert handoffs("draw_scene_picture")[2]["pictures"] == [pictures[1].file, a]
    check = ModelCall.objects.filter(purpose="check_scene_picture").order_by("created_at", "pk")[
        edits
    ]
    assert [image["key"] for image in check.images] == [edited.file, a, b, shared]
    a_line = "Image 1: Copy the shape." if edits == 1 else "Image 1: Copy the round rim."
    assert check.handoff["photos"] == [
        a_line,
        "Image 2: Copy the handle's curve.",
        "Image 3 shows the room alone; keep the same look and light.",
    ]
    clip = ProducedItem.objects.get(kind="clip", scene__number=2)
    assert clip.sent is not None
    assert [p["library_number"] for p in clip.sent["picture_photos"]] == [1, 2]
    for purpose in ("check_clip_references", "check_clip_product", "check_clip_motion"):
        judges = ModelCall.objects.filter(purpose=purpose)
        assert judges.exists()
        for judge in judges:
            references = judge.handoff["references"]
            assert len(references) == 3
            assert references[0] == "Starting picture: what the video model was given."
            assert references[1].endswith(a_line)
            assert references[2].endswith("Image 2: Copy the handle's curve.")
            assert [image["key"] for image in judge.images[:3]] == [edited.file, a, b]


@pytest.mark.parametrize("broken_source", ["missing", "other scene", "loop"])
def test_incomplete_edit_chain_keeps_found_photos_and_records_a_developer_notice(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
    api: Any,
    session_id: str,
    broken_source: str,
) -> None:
    from gateway.fake import meanwhile
    from gateway.models import ModelCall

    from .conftest import chat, recorded

    def damage_source() -> None:
        original = ProducedItem.objects.get(kind="starting_picture", scene__number=2)
        assert original.sent is not None
        if broken_source == "other scene":
            source = ProducedItem.objects.create(
                job=original.job,
                scene=original.job.scenes.get(number=1),
                kind=ProducedItem.Kind.STARTING_PICTURE,
                file=original.file,
                sent={
                    "photos": [
                        {
                            "file": original.file,
                            "line": "Image 1: Wrong scene.",
                            "library_number": 99,
                        }
                    ]
                },
            ).pk
        else:
            source = original.pk if broken_source == "loop" else original.pk + 10000
        original.sent["edited_from"] = source
        original.save(update_fields=["sent"])

    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        meanwhile(
            damage_source,
            turn(
                calls=[
                    (
                        "edit_starting_picture",
                        {
                            "keep": ["The mug, room and hand"],
                            "change": "Show the handle more clearly",
                            "anything_else": "not needed here",
                            "picks": [{"item": 2, "take": "Copy the handle."}],
                        },
                    )
                ]
            ),
        ),
        turn(calls=[("give_up", {"reason": "The edited picture is ready."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    original, edited = ProducedItem.objects.filter(
        kind="starting_picture", scene__number=2
    ).order_by("version")
    assert original.sent is not None and edited.sent is not None
    a, shared = [p["file"] for p in original.sent["photos"]]
    b = edited.sent["photos"][0]["file"]
    check = ModelCall.objects.filter(purpose="check_scene_picture").order_by("created_at", "pk")[1]
    assert [image["key"] for image in check.images] == [edited.file, a, shared, b]
    assert check.handoff["photos"] == [
        "Image 1: Copy the shape and glaze.",
        "Image 2 shows the room alone; keep the same look and light.",
        "Image 3: Copy the handle.",
    ]
    warning = next(
        row
        for row in api.get(f"/api/sessions/{session_id}/warnings/").json()
        if row["kind"] == "picture_evidence_incomplete"
    )
    assert "only the evidence photos found in the edit chain were kept" in warning["text"]
    assert ("problem", warning["text"]) in recorded(api, session_id)
    assert warning["text"] not in [text for _, text in chat(api, session_id)]


def test_sixteen_picks_are_refused_before_any_picture_is_paid_for(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "scene_agent",
        turn(
            calls=[("draw_starting_picture", {**PICTURE_FORM, "picks": PICTURE_FORM["picks"] * 16})]
        ),
        turn(calls=[("give_up", {"reason": "Choose fewer photos."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show use.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    assert "15 at most" in str(handoffs("scene_agent")[-1])
    assert paid_for().count("draw_scene_picture") == 0


@pytest.mark.parametrize("fallback", [False, True])
def test_picked_photo_is_cut_out_and_recoloured_before_draw_and_check(
    fake_model: FakeModel,
    fallback: bool,
    api: Any,
    session_id: str,
    request: pytest.FixtureRequest,
    picture_checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .conftest import LABELLED

    fake_model.respond("label_photo", LABELLED, {**LABELLED, "alone": "no", "colour": "black"})
    request.getfixturevalue("scene_agent_ready")
    fake_model.respond(
        "scene_agent",
        turn(
            calls=[
                (
                    "draw_starting_picture",
                    {**PICTURE_FORM, "picks": [{"item": 2, "take": "Copy the handle."}]},
                )
            ]
        ),
        turn(calls=[("give_up", {"reason": "Need another angle."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show use.", "quotes": ["go on"]}),
    )
    if fallback:
        from gateway.types import BlockedBySafetyFilter

        fake_model.respond(
            "recolour_picture", BlockedBySafetyFilter("refused"), BlockedBySafetyFilter("refused")
        )
    run(fake_model, steps)
    made = ProducedItem.objects.get(kind="starting_picture", scene__number=2)
    assert made.sent is not None
    photos = made.sent["photos"]
    assert photos[0]["file"] != photos[0]["original_file"]
    assert paid_for().count("make_product_only") == 1
    assert paid_for().count("recolour_picture") == (2 if fallback else 1)
    if fallback:
        from .conftest import recorded

        assert "refused to paint it" in str(recorded(api, session_id)).lower()
    assert handoffs("draw_scene_picture")[0]["pictures"] == [p["file"] for p in photos]
    assert handoffs("check_scene_picture")[0]["photos"] == [p["line"] for p in photos]


def test_six_failed_pictures_keep_agent_alive_but_refuse_a_seventh_draw(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    failed = {**PICTURE_PASSES, "physics": {"passes": False, "says": "The object floats."}}
    fake_model.answer_unscripted("check_scene_picture", lambda _: failed)
    fake_model.respond(
        "scene_agent",
        *[turn(calls=[("draw_starting_picture", PICTURE_FORM)]) for _ in range(7)],
        turn(calls=[("give_up", {"reason": "Need a different view."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show use.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    assert paid_for().count("draw_scene_picture") == 6
    assert "All six starting pictures have been drawn" in str(handoffs("scene_agent")[-1])
    assert ProducedItem.objects.filter(kind="starting_picture", scene__number=2).count() == 6
