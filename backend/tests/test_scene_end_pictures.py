"""End pictures through the scene chat, faking only outside services."""

from collections.abc import Callable
from typing import Any

import pytest

from gateway.fake import FakeModel, turn
from gateway.types import BlockedBySafetyFilter, ClipFailed, ToolUse
from jobs.models import ProducedItem

from .conftest import HeldSteps, handoffs, paid_for, recorded, results_of
from .test_clip_judge import product
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_film import FILM
from .test_scene_pictures import FRONT, PAGE_SAYS, PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
END_PICTURE = {
    "keep": ["The hand, room and lighting"],
    "change": "The mug is full of tea beneath the pouring spout",
    "anything_else": "not needed here",
}


def test_end_picture_is_made_and_filmed_from_the_first_try_with_its_cases_in_its_notes(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("make_end_picture", END_PICTURE)]),
        turn(calls=[("film", FILM)]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    assert "End picture" in results_of("make_end_picture")[0]
    assert len(handoffs("make_end_picture")) == 1
    assert "It passes." in results_of("film")[0]
    [clip] = handoffs("make_broll_clip")
    assert clip["end_picture"] is not None
    requests = [request for request in fake_model.turns_taken if request.purpose == "scene_agent"]
    first = requests[0]
    tool = next(tool for tool in first.tools if tool.name == "make_end_picture")
    for words in (" ".join(first.instructions.split()), " ".join(tool.description.split())):
        for case in (
            "result where the product touched",
            "crop of the real label",
            "drifts or ends in the wrong place",
            "stop at a set point",
            "not a limit",
        ):
            assert case in words
    assert "starting picture" in tool.description
    assert "spot the product touched" in (tool.arguments.model_fields["change"].description or "")
    assert "Everything else" in (tool.arguments.model_fields["keep"].description or "")
    assert all(request.instructions == first.instructions for request in requests)
    assert all(request.tools == first.tools for request in requests)


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
    assert "only that changed" in answer.result
    assert "product or hand" in answer.result
    assert "starting picture" in answer.result
    # Each end picture also gets the starting picture's product photos, never the room.
    drawn = ProducedItem.objects.get(kind="starting_picture", file=start)
    assert drawn.sent is not None
    # Photo 2, unpicked, goes again as an extra view, added afresh to each edit (#516).
    [photo, view, _room] = drawn.sent["photos"]
    assert [edit["pictures"] for edit in handoffs("make_end_picture")] == [
        [start, photo["file"], view["file"]]
    ] * (redos + 1)
    for edit in handoffs("make_end_picture"):
        assert "Image 2: Copy the shape and glaze." in edit["prompt"]
        assert "room" not in edit["prompt"].split("Keep everything else")[1]
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
    # The end picture gets the whole chain's product photos; the edit's re-pick of photo 2
    # replaces the first draw's line for it, and the room picture stays out.
    [edit] = handoffs("make_end_picture")
    assert edit["pictures"] == [edited.file, a, b]
    assert "Image 2: Copy the shape. Image 3: Copy the handle's curve." in edit["prompt"]
    assert "Copy the handle." not in edit["prompt"]
    assert "same look and light" not in edit["prompt"]
    assert end.sent is not None
    assert [p["library_number"] for p in end.sent["photos"]] == [1, 2]
    check = ModelCall.objects.filter(purpose="check_scene_picture").order_by("created_at", "pk")[2]
    assert [image["key"] for image in check.images] == [end.file, edited.file, a, b, shared]
    assert check.handoff["photos"] == [
        "Image 1: Copy the shape." + PAGE_SAYS,
        f"Image 2: Copy the handle's curve. (The page photo shows: {FRONT}, side: front; sent "
        "recoloured to sage-green.)",
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


@pytest.mark.parametrize("edit", [False, True], ids=["draws", "edits"])
def test_five_starting_picture_tries_leave_six_end_picture_tries(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    edit: bool,
) -> None:
    from .test_scene_pictures import PICTURE_PASSES

    failed = {**PICTURE_PASSES, "physics": {"passes": False, "says": "The object floats."}}
    fake_model.respond("check_scene_picture", *([failed] * 4), PICTURE_PASSES)
    starting = (
        ("edit_starting_picture", {**END_PICTURE, "picks": PICTURE_FORM["picks"]})
        if edit
        else ("draw_starting_picture", PICTURE_FORM)
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        *[turn(calls=[starting]) for _ in range(4)],
        *[turn(calls=[("make_end_picture", END_PICTURE)]) for _ in range(7)],
        turn(calls=[starting]),
        turn(calls=[starting]),
        turn(calls=[("give_up", {"reason": "Both picture budgets are used."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    assert paid_for().count("make_end_picture") == 6
    assert len(results_of("make_end_picture")) == 7
    assert "All 6 end-picture tries" in results_of("make_end_picture")[-1]
    assert ProducedItem.objects.filter(kind="starting_picture", scene__number=2).count() == 6
    assert "All 6 starting-picture tries" in results_of(starting[0])[-1]
    request = next(r for r in fake_model.turns_taken if r.purpose == "scene_agent")
    assert "six starting-picture tries" in request.instructions
    assert "six end-picture tries" in request.instructions
    assert "6 end-picture tries" in results_of("make_end_picture")[0]


def test_a_talking_scene_films_a_failed_end_picture_only_after_its_six_tries_with_a_notice(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: Any,
    session_id: str,
) -> None:
    from .test_scene_pictures import PICTURE_PASSES

    failed = {**PICTURE_PASSES, "product_same": {"passes": False, "says": "The label is gone."}}
    fake_model.respond("check_scene_picture", PICTURE_PASSES, *([failed] * 6))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        *[turn(calls=[("make_end_picture", END_PICTURE)]) for _ in range(5)],
        turn(calls=[("film", FILM)]),
        turn(calls=[("make_end_picture", END_PICTURE)]),
        turn(calls=[("film", FILM)]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 3, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    assert "end picture failed" in results_of("film")[0]
    assert "6 end-picture tries" in results_of("film")[0]
    assert "It passes." in results_of("film")[1]
    assert paid_for().count("make_end_picture") == 6
    [clip] = handoffs("make_broll_clip")
    last = ProducedItem.objects.filter(kind="end_picture").latest("version")
    assert clip["end_picture"] == last.file
    [notice] = [
        warning
        for warning in api.get(f"/api/sessions/{session_id}/warnings/").json()
        if warning["kind"] == "failed_end_picture_filmed"
    ]
    assert f"end picture {last.pk}" in notice["text"]
    assert "6 end-picture tries" in notice["text"]
    assert ("problem", notice["text"]) in recorded(api, session_id)
    assert not any(
        message["text"] == notice["text"]
        for message in api.get(f"/api/sessions/{session_id}/messages/").json()
    )


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
    # Recorded once, by the loop, naming the scene: not a second time by the tool itself.
    failed = [text for _, text in recorded(api, session_id) if "make_end_picture" in text]
    assert len(failed) == 1
    assert failed[0].startswith(
        "The scene_agent's make_end_picture tool failed on scene 2 (BlockedBySafetyFilter: "
    )
    # With no end picture made, both clips are filmed from the starting picture alone.
    assert all("end_picture" not in sent for sent in handoffs("make_broll_clip"))


def test_an_end_picture_that_cant_be_checked_is_recorded_with_its_scene(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: Any,
    session_id: str,
) -> None:
    from gateway.types import UnusableReply

    from .test_scene_pictures import PICTURE_PASSES

    fake_model.respond("check_clip_product", product("fail", "The mug stays empty."))
    fake_model.respond(
        "check_scene_picture",
        PICTURE_PASSES,
        UnusableReply("No answer", input_tokens=0, output_tokens=0),
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("make_end_picture", END_PICTURE)]),
        turn(calls=[("give_up", {"reason": "The end picture went unchecked."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    assert (
        "problem",
        "Scene 2's end picture could not be checked; the scene agent must inspect it.",
    ) in recorded(api, session_id)


def test_an_end_picture_is_not_asked_the_starting_picture_questions(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .test_scene_pictures import END_PICTURE_PASSES, PICTURE_PASSES

    # Answers that would fail a starting picture: an end picture is judged on the two only.
    would_fail = {
        **END_PICTURE_PASSES,
        "product_ready": {"passes": False, "says": "The pack is sealed."},
    }
    fake_model.respond("check_scene_picture", PICTURE_PASSES, would_fail)
    fake_model.respond("check_clip_product", product("fail", "The mug stays empty."))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("make_end_picture", END_PICTURE)]),
        turn(calls=[("give_up", {"reason": "Enough for this test."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    starting, end = [r for r in fake_model.asked if r.purpose == "check_scene_picture"]
    assert "scene_kind" in starting.handoff.model_dump()
    assert "scene_kind" not in end.handoff.model_dump()
    assert "3. Is the product ready" in starting.instructions
    assert "3. Is the product ready" not in end.instructions
    assert set(end.output.model_fields) == {"product_same", "physics"}
    made = ProducedItem.objects.get(kind="end_picture")
    assert made.ruling == {"status": "passed", "answers": END_PICTURE_PASSES}


@pytest.mark.parametrize("start", ["passed", "failed", "unchecked"])
def test_end_picture_check_fails_only_what_changed_from_a_passed_start(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    start: str,
) -> None:
    from gateway.models import ModelCall
    from gateway.types import UnusableReply

    from .test_scene_pictures import END_PICTURE_PASSES, PICTURE_PASSES

    failed = {**PICTURE_PASSES, "product_same": {"passes": False, "says": "The cap is plain."}}
    start_check: dict[str, Any] | BaseException = {
        "passed": PICTURE_PASSES,
        "failed": failed,
        "unchecked": UnusableReply("no answer", input_tokens=1, output_tokens=1),
    }[start]
    fake_model.respond("check_scene_picture", start_check, END_PICTURE_PASSES)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("make_end_picture", END_PICTURE)]),
        turn(calls=[("give_up", {"reason": "The pair is ready to film."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    drawn = ProducedItem.objects.get(kind="starting_picture", scene__number=2)
    assert drawn.ruling is not None and drawn.ruling["status"] == start
    end = ProducedItem.objects.get(kind="end_picture", scene__number=2)
    assert end.ruling == {"status": "passed", "answers": END_PICTURE_PASSES}
    first, check = ModelCall.objects.filter(purpose="check_scene_picture").order_by(
        "created_at", "pk"
    )
    # The end check keeps the starting picture's product photos, views and room.
    photos = sorted(image["key"] for image in first.images[1:])
    keys = [image["key"] for image in check.images]
    assert check.system_prompt is not None
    told = check.system_prompt.instructions
    if start == "passed":
        # The passed starting picture goes beside the end picture, and only changes fail.
        assert keys[:2] == [end.file, drawn.file]
        assert sorted(keys[2:]) == photos
        assert check.images[0]["label"].startswith("The end picture to check")
        assert "starting picture it was edited from" in check.images[1]["label"]
        assert "shown the same as in the starting picture never fails" in told
        assert "a thing passing through another" in told
    else:
        # A failed or unchecked start gives nothing to compare with: the end is judged whole.
        assert keys[0] == end.file
        assert sorted(keys[1:]) == photos
        assert check.images[0]["label"].startswith("The end picture to check")
        assert "as in the starting picture" not in told
