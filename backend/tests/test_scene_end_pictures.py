"""End pictures through the scene chat, faking only outside services."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn
from gateway.types import BlockedBySafetyFilter, ClipFailed, ToolUse

from .conftest import HeldSteps, handoffs, paid_for, recorded, results_of
from .test_clip_judge import product
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
END_PICTURE = {
    "keep": ["The hand, room and lighting"],
    "change": "The mug is full of tea beneath the pouring spout",
    "anything_else": "not needed here",
}


def test_end_picture_waits_for_a_product_failure_with_its_notes_always_available(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("check_clip_product", product("fail", "The mug stays empty."))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("make_end_picture", END_PICTURE)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("make_end_picture", END_PICTURE)]),
        turn(calls=[("give_up", {"reason": "The pair is ready to film."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    answers = results_of("make_end_picture")
    assert "failed the product check" in answers[0]
    assert "End picture" in answers[1]
    assert len(handoffs("make_end_picture")) == 1
    assert "end picture" not in results_of("film")[0].lower()
    requests = [request for request in fake_model.turns_taken if request.purpose == "scene_agent"]
    first = requests[0]
    assert "use make_end_picture and follow its notes" in first.instructions
    tool = next(tool for tool in first.tools if tool.name == "make_end_picture")
    assert "failed product check" in tool.description
    assert "starting picture" in tool.description
    assert "spot the product touched" in (tool.arguments.model_fields["change"].description or "")
    assert "Everything else" in (tool.arguments.model_fields["keep"].description or "")
    assert all(request.instructions == first.instructions for request in requests)
    assert all(request.tools == first.tools for request in requests)


@pytest.mark.parametrize("failed_check", ["words", "owner change"])
def test_only_a_product_failure_opens_the_end_picture_tool(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    failed_check: str,
) -> None:
    from .conftest import CLIP_MOTION_OK

    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    if failed_check == "words":
        fake_model.respond("transcribe_line", {"text": "This is a completely different claim."})
        fake_model.respond("check_words", {"verdict": "fail", "reason": "The claim changed."})
    else:
        fake_model.respond(
            "check_clip_motion",
            {
                **CLIP_MOTION_OK,
                "owner_change": {
                    "old_shows": "The handle is hidden.",
                    "new_shows": "The handle is still hidden.",
                    "verdict": "not fixed",
                    "still_wrong": "The handle is still hidden.",
                },
            },
        )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("film", FILM)]),
        turn(calls=[("make_end_picture", END_PICTURE)]),
        turn(calls=[("give_up", {"reason": "Fix the failed check first."})]),
    )
    calling(
        fake_model,
        say,
        (
            "pass_note",
            {"scenes": [{"scene": 2, "instruction": "Show the handle.", "quotes": ["go on"]}]},
        ),
    )
    run(fake_model, steps)

    film = results_of("film")[-1]
    assert "It fails." in film
    assert "Word check: fail" in film if failed_check == "words" else "change is made: no" in film
    assert "failed the product check yet" in results_of("make_end_picture")[-1]
    assert not handoffs("make_end_picture")


def test_an_earlier_run_product_failure_opens_the_end_picture_tool(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("check_clip_product", product("fail", "The mug stays empty."))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "Try the result again later."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("make_end_picture", END_PICTURE)]),
        turn(calls=[("give_up", {"reason": "The pair is ready to film."})]),
    )
    calling(
        fake_model,
        say,
        (
            "pass_note",
            {"scenes": [{"scene": 2, "instruction": "Try the result.", "quotes": ["go on"]}]},
        ),
    )
    run(fake_model, steps)
    assert "End picture" in results_of("make_end_picture")[-1]
    assert len(handoffs("make_end_picture")) == 1


@pytest.mark.parametrize("redos", [0, 1])
def test_end_picture_is_checked_beside_its_start_and_sent_to_h3(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    redos: int,
) -> None:
    fake_model.respond("check_clip_product", product("fail", "The mug stays empty."))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("make_end_picture", END_PICTURE)]),
        *[turn(calls=[("make_end_picture", END_PICTURE)]) for _ in range(redos)],
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "The clip should pass."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    request = [r for r in fake_model.turns_taken if r.purpose == "scene_agent"][-1]
    answers = [
        item
        for item in request.handoff.conversation
        if isinstance(item, ToolUse) and item.tool == "make_end_picture"
    ]
    answer = answers[-1]
    assert len(answer.pictures) == 2, results_of("make_end_picture")
    start, end = [image.key for image in answer.pictures]
    assert answer.pictures[0].label.startswith("Starting picture")
    assert answer.pictures[1].label.startswith("End picture")
    assert "The product matches." in answer.result
    assert "only that spot changed" in answer.result
    assert "product or hand" in answer.result
    assert "starting picture" in answer.result
    assert [edit["pictures"] for edit in handoffs("make_end_picture")] == [[start]] * (redos + 1)
    assert len({item.pictures[1].key for item in answers}) == redos + 1
    assert handoffs("make_broll_clip")[0].get("end_picture") is None
    filmed = handoffs("make_broll_clip")[1]
    assert filmed["starting_picture"] == start
    assert filmed["end_picture"] == end
    assert "End picture" in results_of("film")[-1]


def test_end_picture_check_keeps_the_edited_starting_pictures_evidence(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from gateway.models import ModelCall
    from jobs.models import ProducedItem

    fake_model.respond("check_clip_product", product("fail", "The mug stays empty."))
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
        turn(calls=[("film", FILM)]),
        turn(
            calls=[
                (
                    "edit_starting_picture",
                    {
                        **END_PICTURE,
                        "change": "Show the handle more clearly",
                        "picks": [{"item": 2, "take": "Copy the handle's curve."}],
                    },
                )
            ]
        ),
        turn(calls=[("make_end_picture", END_PICTURE)]),
        turn(calls=[("give_up", {"reason": "The pair is ready to film."})]),
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
    end = ProducedItem.objects.get(kind="end_picture", scene__number=2)
    assert original.sent is not None
    a, b, shared = [p["file"] for p in original.sent["photos"]]
    assert handoffs("make_end_picture")[0]["pictures"] == [edited.file]
    check = ModelCall.objects.filter(purpose="check_scene_picture").order_by("created_at", "pk")[2]
    assert [image["key"] for image in check.images] == [end.file, a, b, shared]
    assert check.handoff["photos"] == [
        "Image 1: Copy the shape.",
        "Image 2: Copy the handle's curve.",
        "Image 3 shows the room alone; keep the same look and light.",
    ]


@pytest.mark.parametrize("edit", [False, True])
def test_new_starting_picture_drops_its_old_end_picture(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    edit: bool,
) -> None:
    fake_model.respond("check_clip_product", product("fail", "The mug stays empty."))
    replacement = (
        (
            "edit_starting_picture",
            {
                **END_PICTURE,
                "change": "The mug is closer to the camera",
                "picks": PICTURE_FORM["picks"],
            },
        )
        if edit
        else ("draw_starting_picture", {**PICTURE_FORM, "camera": "Closer to the mug"})
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("make_end_picture", END_PICTURE)]),
        turn(calls=[replacement]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "The clip should pass."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    assert "end picture was dropped" in results_of(replacement[0])[-1]
    filmed = handoffs("make_broll_clip")
    assert len(filmed) == 2
    assert filmed[-1]["starting_picture"] != filmed[0]["starting_picture"]
    assert "end_picture" not in filmed[-1]


def test_h3_refusal_of_an_end_picture_is_told_to_agent_and_recorded_for_developer(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: Any,
    session_id: str,
) -> None:
    fake_model.respond("check_clip_product", product("fail", "The mug stays empty."))
    fake_model.respond("make_clip", {}, ClipFailed("The end image is refused"))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("make_end_picture", END_PICTURE)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "The end picture was refused."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    assert "H3 refused" in results_of("film")[-1]
    assert any("H3 refused" in text for _, text in recorded(api, session_id))
    assert not any(
        "H3 refused" in message["text"]
        for message in api.get(f"/api/sessions/{session_id}/messages/").json()
    )


def test_end_pictures_share_the_six_picture_slots(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond("check_clip_product", product("fail", "The mug stays empty."))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        *[turn(calls=[("make_end_picture", END_PICTURE)]) for _ in range(6)],
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("give_up", {"reason": "The picture slots are used."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    assert paid_for().count("draw_scene_picture") == 1
    assert paid_for().count("make_end_picture") == 5
    assert "six picture slots" in results_of("make_end_picture")[-1]
    assert "All six" in results_of("draw_starting_picture")[-1]


def test_missing_start_and_failed_end_picture_are_explained_without_swapping_the_start(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: Any,
    session_id: str,
) -> None:
    fake_model.respond("check_clip_product", product("fail", "The mug stays empty."))
    fake_model.respond("make_end_picture", BlockedBySafetyFilter("The edit is refused"))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("make_end_picture", END_PICTURE)]),
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("make_end_picture", END_PICTURE)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "The clip should pass."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    answers = results_of("make_end_picture")
    assert "no starting picture" in answers[0]
    assert "safety filter refused" in answers[1]
    assert any("end picture could not be made" in text for _, text in recorded(api, session_id))
    sent = handoffs("make_broll_clip")
    assert sent[0]["starting_picture"] == sent[1]["starting_picture"]
    assert "end_picture" not in sent[1]
