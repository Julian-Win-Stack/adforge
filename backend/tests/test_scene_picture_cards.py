"""B-roll pictures and their clips on the owner's cards, through the chat."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from gateway.types import Turn

from .conftest import HeldSteps
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("legacy_path")]


def test_picture_is_on_the_card_before_filming_then_joins_its_clip(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    def before_film() -> Turn:
        response = api.get(f"/api/sessions/{session_id}/scenes/")
        card = next(each for each in response.json() if each["scene"] == 2)
        assert card["picture"]
        assert card["versions"] == []
        assert card["picture"] in str(api.get(f"/api/sessions/{session_id}/messages/").json())
        return turn(calls=[("film", FILM)])

    fake_model.respond(
        "scene_agent", turn(calls=[("draw_starting_picture", PICTURE_FORM)]), before_film
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    response = api.get(f"/api/sessions/{session_id}/scenes/")
    card = next(each for each in response.json() if each["scene"] == 2)
    assert len(card["versions"]) == 1
    assert card["versions"][0]["picture"] == card["picture"]
    messages = api.get(f"/api/sessions/{session_id}/messages/").json()
    signals = [message["step"] for message in messages if message["step"] is not None]
    assert signals[-1] == {"scene": 2, "kind": "clip", "state": "made"}


@pytest.mark.parametrize(
    "edit",
    [
        ("edit_scene", {"scene": 2, "line": "A warm drink waits in this mug."}),
        ("edit_scene", {"scene": 2, "shows": "A hand turns the mug on the desk."}),
        ("set_place", {"scene": 2, "new_place": "A desk beside a tall window."}),
    ],
)
def test_an_edit_makes_the_broll_starting_picture_and_clip_stale(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    edit: tuple[str, dict[str, str | int]],
) -> None:
    from .conftest import results_of

    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    calling(fake_model, say, edit)
    answer = results_of(edit[0])[-1]
    assert "starting picture" in answer and "clip" in answer and "stale" in answer


def test_only_the_filmed_picture_is_kept_as_a_version_and_the_look_sees_it(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    from .conftest import handoffs
    from .test_scene_pictures import PICTURE_PASSES

    fake_model.respond(
        "check_scene_picture",
        {**PICTURE_PASSES, "physics": {"passes": False, "says": "The hand floats."}},
        PICTURE_PASSES,
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    card = next(
        each for each in api.get(f"/api/sessions/{session_id}/scenes/").json() if each["scene"] == 2
    )
    assert len(card["versions"]) == 1
    fake_model.respond(
        "look",
        {
            "sees": "The starting picture and its clip.",
            "wrong": "Nothing",
            "root_cause": "",
            "direction": "",
        },
    )
    calling(
        fake_model,
        say,
        (
            "look",
            {
                "scene": 2,
                "what": "version",
                "number": 1,
                "question": "Does the clip start like the picture?",
            },
        ),
    )
    request = next(each for each in fake_model.asked if each.purpose == "look")
    assert request.images[0].key in card["versions"][0]["picture"]
    assert "starting picture" in request.images[0].label.lower()
    assert handoffs("look")


@pytest.mark.parametrize("changed_number,remove", [(1, True), (1, False), (2, True)])
def test_only_a_photo_used_by_the_picture_makes_it_stale_when_the_library_changes(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    changed_number: int,
    remove: bool,
) -> None:
    from adforge import file_store
    from jobs.models import Job

    from .conftest import picture, results_of

    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    # No owner tool removes or replaces an existing library row yet. Change that input
    # at its stored boundary, then observe the real producer edit's stale answer.
    source = Job.objects.get().library.get(number=changed_number)
    if remove:
        source.delete()
    else:
        source.file = file_store.save("replacement.png", picture(30, 40, (90, 100, 110)))
        source.save(update_fields=["file"])
    calling(fake_model, say, ("edit_scene", {"scene": 2, "overlay": "A new caption"}))
    answer = results_of("edit_scene")[-1]
    assert ("starting picture and clip are stale" in answer) is (changed_number == 1)


def test_a_removed_picture_source_is_refused_before_paying_for_film(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from jobs.models import Job

    from .conftest import paid_for, results_of

    def source_removed() -> Turn:
        Job.objects.get().library.filter(number=1).delete()
        return turn(calls=[("film", FILM)])

    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        source_removed,
        turn(calls=[("give_up", {"reason": "A replacement photo is needed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    assert "out of date" in results_of("film")[-1]
    assert "make_broll_clip" not in paid_for()


def test_an_abandoned_redraw_keeps_the_existing_clip_and_its_picture_current(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    from .conftest import handoffs, results_of

    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show its use.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    before = next(
        each for each in api.get(f"/api/sessions/{session_id}/scenes/").json() if each["scene"] == 2
    )
    calling(
        fake_model,
        say,
        (
            "pass_note",
            {"scenes": [{"scene": 2, "instruction": "Try another angle.", "quotes": ["go on"]}]},
        ),
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", {**PICTURE_FORM, "camera": "Phone at eye height"})]),
        turn(calls=[("give_up", {"reason": "The new angle cannot be filmed."})]),
    )
    run(fake_model, steps)
    draws = handoffs("check_scene_picture")
    assert len(draws) == 2
    checked_pictures = [
        request.images[0].key
        for request in fake_model.asked
        if request.purpose == "check_scene_picture"
    ]
    assert checked_pictures[0] != checked_pictures[1]
    after = next(
        each for each in api.get(f"/api/sessions/{session_id}/scenes/").json() if each["scene"] == 2
    )
    assert after["picture"] == before["picture"]
    assert after["versions"][-1]["url"] == before["versions"][-1]["url"]
    calling(fake_model, say, ("edit_scene", {"scene": 2, "overlay": "A different caption"}))
    answer = results_of("edit_scene")[-1]
    assert "Scene 2's clip is stale" not in answer
    assert "starting picture and clip are stale" not in answer


@pytest.mark.parametrize("picks", [[], [{"item": 2, "take": "Copy the handle."}]])
@pytest.mark.parametrize("remove", [False, True])
def test_edited_picture_and_clip_keep_the_original_draws_photo_dependencies(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    picks: list[dict[str, str | int]],
    remove: bool,
) -> None:
    from adforge import file_store
    from jobs.models import Job

    from .conftest import facts_ok, paid_for, picture, results_of
    from .test_producer_broll import SHOWS
    from .test_scene_agent_changes import change

    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        # The edit captures this new row; only source-photo dependencies survive
        # from its ancestor, not the old line/action snapshot.
        turn(calls=[change(line="This mug holds a warm drink.", shows=SHOWS)]),
        turn(
            calls=[
                (
                    "edit_starting_picture",
                    {
                        "keep": ["the room"],
                        "change": "Make the handle clearer.",
                        "anything_else": "not needed here",
                        "picks": picks,
                    },
                )
            ]
        ),
        turn(calls=[("film", FILM)]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {
                "scene": 2,
                "instruction": "Show its use.",
                "quotes": ["Go on"],
            },
        ),
    )
    run(fake_model, steps)
    source = Job.objects.get().library.get(number=1)
    if remove:
        source.delete()
    else:
        source.file = file_store.save("new-source.png", picture(30, 40, (80, 90, 100)))
        source.save(update_fields=["file"])
    calling(fake_model, say, ("edit_scene", {"scene": 2, "overlay": "A new caption"}))
    assert "starting picture and clip are stale" in results_of("edit_scene")[-1]
    before = paid_for().count("make_broll_clip")
    fake_model.respond(
        "scene_agent",
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "Need a new photo."})]),
    )
    calling(
        fake_model,
        say,
        (
            "pass_note",
            {
                "scenes": [
                    {
                        "scene": 2,
                        "instruction": "Film again.",
                        "quotes": ["Go on"],
                    }
                ]
            },
        ),
    )
    run(fake_model, steps)
    assert "out of date" in results_of("film")[-1]
    assert paid_for().count("make_broll_clip") == before
