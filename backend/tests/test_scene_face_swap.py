"""Strangers are replaced at the scene-agent/gateway seam before drawing or editing."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, meanwhile, turn

from .conftest import HeldSteps, handoffs, paid_for
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
FACE_PASSES = {
    "features": "Every face has different eyes and nose.",
    "verdict": "pass",
    "problem": None,
}
EDIT = {
    "keep": ["the product"],
    "change": "Show the handle more clearly",
    "anything_else": "not needed here",
    "picks": PICTURE_FORM["picks"],
}


@pytest.fixture
def stranger_photo(fake_model: FakeModel, request: pytest.FixtureRequest) -> None:
    fake_model.answer_unscripted("note_face", lambda _: {"has_face": True})
    fake_model.answer_unscripted("check_face_swap", lambda _: FACE_PASSES)
    request.getfixturevalue("scene_agent_ready")


def draw_and_edit(
    fake_model: FakeModel,
    steps: HeldSteps,
    say: Callable[..., None],
    *,
    item: int = 1,
    without_presenter: bool = False,
) -> None:
    from jobs.models import Job, ProducedItem

    picks = [{"item": item, "take": "Copy the shape and glaze."}]
    # The owner's photo is of the ad's product, as the plan's product photos say.
    Job.objects.update(product_photos=[1, item])
    drawing = turn(calls=[("draw_starting_picture", {**PICTURE_FORM, "picks": picks})])

    def remove_presenter() -> None:
        # Seed the historical no-presenter state after the producer's readiness check: the
        # room alone drawn from the before-picture, as an ad with no presenter has it.
        ProducedItem.objects.filter(kind="room_alone").update(
            made_from=ProducedItem.objects.get(kind="before_picture")
        )
        ProducedItem.objects.filter(person__kind="portrait").delete()
        ProducedItem.objects.filter(kind="portrait").delete()

    fake_model.respond(
        "scene_agent",
        meanwhile(remove_presenter, drawing) if without_presenter else drawing,
        turn(calls=[("edit_starting_picture", {**EDIT, "picks": picks})]),
        turn(calls=[("give_up", {"reason": "Need another angle."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["Go on"]}),
    )
    run(fake_model, steps)


def test_stranger_is_swapped_before_drawing_and_reused_for_editing(
    fake_model: FakeModel,
    stranger_photo: None,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    draw_and_edit(fake_model, steps, say)
    swap = handoffs("swap_face")
    assert len(swap) == 1
    draws = handoffs("draw_scene_picture")
    check = next(request for request in fake_model.asked if request.purpose == "check_face_swap")
    assert [image.key for image in check.images] == [
        swap[0]["pictures"][0],
        draws[0]["pictures"][0],
    ]
    assert not handoffs("recolour_picture")
    assert draws[0]["pictures"][0] != swap[0]["pictures"][0]
    assert draws[1]["pictures"][1] == draws[0]["pictures"][0]
    assert paid_for().count("check_face_swap") == 1


@pytest.mark.parametrize("failure", ["checks", "refusals", "no presenter"])
def test_failed_swap_sends_original_and_posts_one_red_warning(
    fake_model: FakeModel,
    stranger_photo: None,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    failure: str,
) -> None:
    from gateway.types import BlockedBySafetyFilter

    from .conftest import chat, recorded

    if failure == "checks":
        fake_model.answer_unscripted(
            "check_face_swap",
            lambda _: {
                "features": "The eyes are unchanged.",
                "verdict": "fail",
                "problem": "The face stayed.",
            },
        )
    elif failure == "refusals":
        fake_model.respond(
            "swap_face", BlockedBySafetyFilter("refused"), BlockedBySafetyFilter("refused")
        )
    draw_and_edit(fake_model, steps, say, without_presenter=failure == "no presenter")
    draws = handoffs("draw_scene_picture")
    original = (
        next(request for request in fake_model.asked if request.purpose == "note_face")
        .images[0]
        .key
    )
    assert draws[0]["pictures"][0] == original
    assert draws[1]["pictures"][1] == original
    warnings = [
        (level, text) for level, text in recorded(api, session_id) if "real face may show" in text
    ]
    assert len(warnings) == 1
    assert warnings[0][0] == "problem"
    assert sum("real face may show" in text for _, text in chat(api, session_id)) == 1
    assert paid_for().count("swap_face") == {"checks": 3, "refusals": 2, "no presenter": 0}[failure]


def test_new_presenter_gets_a_new_swap(
    fake_model: FakeModel,
    stranger_photo: None,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    draw_and_edit(fake_model, steps, say)
    calling(fake_model, say, ("remake_person", {"remake": "look", "instruction": "Shorter hair"}))
    draw_and_edit(fake_model, steps, say)
    swaps = handoffs("swap_face")
    assert len(swaps) == 2
    assert swaps[0]["pictures"][0] == swaps[1]["pictures"][0]
    assert swaps[0]["pictures"][1] != swaps[1]["pictures"][1]
    draws = handoffs("draw_scene_picture")
    assert draws[0]["pictures"][0] != draws[2]["pictures"][0]


@pytest.mark.parametrize("cut_out", [False, True])
def test_no_face_or_product_only_cut_out_is_not_swapped(
    fake_model: FakeModel,
    request: pytest.FixtureRequest,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    cut_out: bool,
) -> None:
    from .conftest import LABELLED

    if cut_out:
        fake_model.answer_unscripted("note_face", lambda _: {"has_face": True})
        fake_model.answer_unscripted("label_photo", lambda _: {**LABELLED, "alone": "no"})
    request.getfixturevalue("scene_agent_ready")
    draw_and_edit(fake_model, steps, say)
    assert len(handoffs("draw_scene_picture")) == 2
    assert "swap_face" not in paid_for()
    assert paid_for().count("make_product_only") == (1 if cut_out else 0)


def test_swapped_photo_is_recoloured_before_drawing(
    fake_model: FakeModel,
    request: pytest.FixtureRequest,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .conftest import LABELLED

    fake_model.respond("label_photo", LABELLED, {**LABELLED, "colour": "black"})
    request.getfixturevalue("stranger_photo")
    draw_and_edit(fake_model, steps, say, item=2)
    swap = handoffs("swap_face")[0]
    paint = handoffs("recolour_picture")[0]
    draw = handoffs("draw_scene_picture")[0]
    check = next(request for request in fake_model.asked if request.purpose == "check_face_swap")
    assert paint["pictures"][0] == check.images[1].key
    assert paint["pictures"][0] != swap["pictures"][0]
    assert draw["pictures"][0] not in (swap["pictures"][0], paint["pictures"][0])
    assert paid_for().count("swap_face") == 1
    assert paid_for().count("recolour_picture") == 1


def test_worker_stop_replays_paid_draws_and_checks_without_paying_again(
    fake_model: FakeModel,
    stranger_photo: None,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from agents import tasks

    from .conftest import WorkerStopped

    fake_model.respond(
        "check_face_swap",
        {"features": "Same eyes.", "verdict": "fail", "problem": "The eyes stayed."},
        WorkerStopped(),
        FACE_PASSES,
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("give_up", {"reason": "Need another angle."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["Go on"]}),
    )
    with pytest.raises(WorkerStopped):
        steps.run_held()
    fake_model.respond("produce", turn(says="Done."))
    tasks.carry_on_after_the_worker_starts()
    steps.run_held()
    assert paid_for().count("swap_face") == 2
    assert paid_for().count("check_face_swap") == 2
    assert paid_for().count("draw_scene_picture") == 1
    assert fake_model.left("check_face_swap") == 0


@pytest.mark.parametrize("purpose", ["swap_face", "check_face_swap"])
def test_unavailable_swap_is_recorded_and_does_not_reach_the_picture_model(
    fake_model: FakeModel,
    stranger_photo: None,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    purpose: str,
) -> None:
    from adforge.retry import OutsideServiceDown

    from .conftest import recorded

    fake_model.respond(purpose, *[OutsideServiceDown("service unavailable")] * 3)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("give_up", {"reason": "The picture service is unavailable."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["Go on"]}),
    )
    run(fake_model, steps)
    assert not handoffs("draw_scene_picture")
    assert any(
        "face swap" in text.lower() and "service unavailable" in text
        for _, text in recorded(api, session_id)
    )


def test_refused_swap_that_later_passes_is_recorded(
    fake_model: FakeModel,
    stranger_photo: None,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    from gateway.types import BlockedBySafetyFilter

    from .conftest import recorded

    fake_model.respond("swap_face", BlockedBySafetyFilter("refused"))
    draw_and_edit(fake_model, steps, say)
    assert len(handoffs("draw_scene_picture")) == 2
    assert any(
        "face swap" in text.lower() and "refused" in text for _, text in recorded(api, session_id)
    )


def test_owner_photo_marked_with_a_face_is_swapped_before_drawing(
    fake_model: FakeModel,
    request: pytest.FixtureRequest,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .conftest import MUG_FRONT

    request.getfixturevalue("page_read")
    fake_model.answer_unscripted("note_face", lambda _: {"has_face": True})
    fake_model.answer_unscripted("check_face_swap", lambda _: FACE_PASSES)
    fake_model.respond(
        "produce",
        turn(calls=[("use_photos", {"photos": [{"photo": 1, "shows": "the product"}]})]),
        turn(says="Added your photo."),
    )
    say("Use this photo of the product", ("owner.png", MUG_FRONT))
    request.getfixturevalue("scene_agent_ready")
    draw_and_edit(fake_model, steps, say, item=3)
    swaps = handoffs("swap_face")
    owner_photo = (
        next(request for request in reversed(fake_model.asked) if request.purpose == "note_face")
        .images[0]
        .key
    )
    assert swaps[0]["pictures"][0] == owner_photo
    assert handoffs("draw_scene_picture")[0]["pictures"][0] != owner_photo
    assert paid_for().count("swap_face") == 1


@pytest.mark.parametrize("uncheckable", ["unreadable picture", "unusable answer"])
def test_uncheckable_swap_retains_its_notice_when_a_later_try_passes(
    fake_model: FakeModel,
    stranger_photo: None,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    uncheckable: str,
) -> None:
    from gateway.gateway import UnreadableImage
    from gateway.types import UnusableReply

    from .conftest import chat, recorded

    error = (
        UnreadableImage("The picture could not be read")
        if uncheckable == "unreadable picture"
        else UnusableReply("The answer could not be read", input_tokens=1_000, output_tokens=100)
    )
    fake_model.respond("check_face_swap", error, FACE_PASSES)
    draw_and_edit(fake_model, steps, say)
    checks = [request for request in fake_model.asked if request.purpose == "check_face_swap"]
    draws = handoffs("draw_scene_picture")
    assert draws[0]["pictures"][0] == checks[-1].images[1].key
    assert draws[1]["pictures"][1] == draws[0]["pictures"][0]
    assert any(
        "face swap check" in text.lower() and str(error) in text
        for _, text in recorded(api, session_id)
    )
    assert not any("face swap check" in text.lower() for _, text in chat(api, session_id))
