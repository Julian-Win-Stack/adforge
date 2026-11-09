"""The scene agent films its checked picture through the owner's chat."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gateway.fake import FakeModel, turn

from .conftest import HeldSteps, paid_for, results_of
from .test_producer_broll import calling, checked, run  # noqa: F401
from .test_scene_agent import scene_agent_ready  # noqa: F401
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
FILM: dict[str, Any] = {
    "form": {
        "shot_size": "Close shot",
        "frame_shows": "Hands hold the mug at a desk",
        "product": "The glazed mug",
        "camera": "The phone drifts gently left",
        "beats": ["Tea pours into the mug", "Steam rises"],
        "shows_up_later": "not needed here",
        "small_life": "The fingers shift slightly",
        "lock_line": "The mug keeps its exact shape, colour and printed words",
        "anything_else": "not needed here",
        "after_last_word": "The hand finishes pouring and settles the teapot beside the mug",
    }
}


@pytest.mark.parametrize("scene", [2, 3], ids=["broll-unchanged", "talking-order"])
def test_film_sends_the_scene_kinds_prompt_with_code_owned_speech(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    scene: int,
) -> None:
    from .conftest import handoffs

    form = {
        **FILM["form"],
        **(
            {
                "shot_size": "selfie close-up",
                "camera": "The phone is perfectly steady at eye level",
            }
            if scene == 3
            else {}
        ),
        "frame_shows": "The woman holds the mug at a desk",
        "shows_up_later": "Steam curls above the rim",
        "anything_else": "Sunlight falls across the desk",
        "delivery": "Warm and pleased" if scene == 3 else "Clear ad pace with high-energy",
        "product_move": "At the end of the first sentence she lifts the mug and brings it back, "
        "its photographed side toward the camera",
        "feeling": "She raises her eyebrows, smiles during the line and nods at its end",
        "sounds": "Room tone, a quiet kettle hum",
    }
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", {"form": form})]),
        turn(calls=[("give_up", {"reason": "The clip should pass."})]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": scene, "instruction": "Show the mug.", "quotes": ["go on"]},
        ),
    )
    run(fake_model, steps)

    request = next(each for each in fake_model.turns_taken if each.purpose == "scene_agent")
    film = next(tool for tool in request.tools if tool.name == "film")
    assert (
        "In a talking scene call her the woman, never the presenter, in every box."
        in film.arguments.model_json_schema()["$defs"]["VideoForm"]["description"]
    )
    boxes = film.arguments.model_json_schema()["$defs"]["VideoForm"]["properties"]
    assert "handheld" not in boxes["shot_size"]["description"]
    for rule in (
        "even in a selfie she holds",
        "never write sway, shake, wobble, drift or handheld movement",
        "a slow push in is the only camera move allowed",
    ):
        assert rule in boxes["camera"]["description"]
    (filmed,) = handoffs("make_broll_clip")
    common = (
        "Film this in real-time speed. Movement starts from the very first frame, never a still "
        "opening. "
    )
    if scene == 3:
        common += (
            "Live-action vertical phone footage, UGC, filmed on a steady phone, selfie close-up. "
            "The woman holds the mug at a desk. The glazed mug. "
            "The phone is perfectly steady at eye level. "
            "The phone stays perfectly steady the whole time: no sway, no shake. "
        )
    else:
        common += (
            "Live-action vertical phone footage, UGC, Close shot. "
            "The woman holds the mug at a desk. The glazed mug. The phone drifts gently left. "
        )
    common += "Tea pours into the mug, then steam rises. Steam curls above the rim. "
    if scene == 3:
        assert filmed["prompt"] == (
            common + "Sunlight falls across the desk. "
            "She talks straight to the lens, warm and pleased, and says with "
            "A warm, relaxed woman in her thirties with a soft British accent: "
            "<d>[English] Yours for $24.00, get your Stoneware Mug from Kiln & Co today.</d> "
            "Her mouth moves in sync with every word. At the end of the first sentence "
            "she lifts the mug and brings it back, its photographed side toward the camera. "
            "She raises her eyebrows, smiles during "
            "the line and nods at its end. After the last word, the hand finishes pouring "
            "and settles the teapot beside the mug. "
            "The mug keeps its exact shape, colour and printed words. "
            "Sound: room tone, a quiet kettle hum, her clear close voice. No music."
        )
        assert "presenter" not in filmed["prompt"]
        assert "handheld" not in filmed["prompt"]
    else:
        assert filmed["prompt"] == (
            common + "The fingers shift slightly. Sunlight falls across the desk. "
            "After the last word, the hand finishes pouring and settles the teapot "
            "beside the mug. The mug keeps its exact shape, colour and printed words. "
            "Off camera, the presenter says with "
            "A warm, relaxed woman in her thirties with a soft British accent: "
            "<d>[English] Hand-thrown, holds 350 ml, and dishwasher safe too. "
            "At home, every day.</d> "
            "Sound: room tone, the action's sounds and the presenter's voice. No music."
        )


def test_talking_delivery_is_filmed_as_written_without_a_word_refusal(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    # Julian, Nov 9 2026: the delivery-word refusal never fired on a real clip, so the form's
    # instructions carry the rule and filming takes the delivery as written.
    from .conftest import handoffs

    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", {"form": {**FILM["form"], "delivery": "Confident and precise"}})]),
        turn(calls=[("give_up", {"reason": "The clip should pass."})]),
    )
    calling(
        fake_model,
        say,
        (
            "start_scene_agent",
            {"scene": 3, "instruction": "Show the mug.", "quotes": ["go on"]},
        ),
    )
    run(fake_model, steps)

    (filmed,) = handoffs("make_broll_clip")
    assert (
        "She talks straight to the lens, confident and precise, and says with" in filmed["prompt"]
    )


@pytest.mark.parametrize("motion_answer", ["description", "old_failures", "unreadable"])
def test_product_pass_keeps_the_clip_and_its_description_without_motion_checks(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    motion_answer: str,
    api: Any,
    session_id: str,
    tmp_path: Path,
) -> None:
    from gateway.types import UnusableReply

    from .conftest import CLIP_MOTION_OK, chat, handoffs, recorded
    from .test_producer_broll import BROLL_PLAN
    from .test_scene_pictures import PICTURE_FORM

    fake_model.respond("transcribe_line", {"text": BROLL_PLAN["plan"]["scenes"][1]["line"]})
    description = {key: CLIP_MOTION_OK[key] for key in ("look", "ordered_action", "seen_action")}
    answer = (
        {
            **description,
            **{
                question: {"verdict": "fail", "problem": "Motion fault", "back_to_normal_at": None}
                for question in ("real_motion", "sudden_change", "does_what_was_ordered")
            },
        }
        if motion_answer == "old_failures"
        else description
    )
    fake_model.respond(
        "check_clip_motion",
        *(
            [UnusableReply("unreadable", input_tokens=0, output_tokens=0)] * 2
            if motion_answer == "unreadable"
            else [answer]
        ),
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "Should have passed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    assert paid_for().count("make_broll_clip") == 1, results_of("film")
    assert "passed" in str(handoffs("produce"))
    words = str(results_of("film"))
    for question in ("real_motion", "sudden_change", "does_what_was_ordered"):
        assert question not in words
    if motion_answer != "unreadable":
        assert "Tea is poured into the mug." in words
        assert "Tea pours into the mug." in words
        assert "A mug on a workbench, tea pouring into it." in words
        assert "At the start: The mug is empty." in words
        assert "At the end: The mug is full." in words
        assert "The action ordered: Pour tea into the mug." in words
    card = next(
        row for row in api.get(f"/api/sessions/{session_id}/scenes/").json() if row["scene"] == 2
    )
    assert card["versions"][-1]["verdict"] == "pass"
    assert not card["versions"][-1]["problem"]
    unavailable = (
        "The clip judge couldn't describe scene 2's clip or check any requested change. "
        "The product check still decides its verdict."
    )
    if motion_answer == "unreadable":
        assert ("problem", unavailable) in recorded(api, session_id)
        assert unavailable not in [text for _, text in chat(api, session_id)]
    else:
        assert unavailable not in [text for _, text in recorded(api, session_id)]
    import json

    from evals import grading_page, round_export

    round_export.export_run({"mug": session_id}, tmp_path, "Product check only")
    exported = json.loads((tmp_path / "results.json").read_text())["mug:2"]
    assert exported["tries"][0]["ruling"]["verdict"] == "pass"
    assert exported["tries"][0]["ruling"]["checks"] == {"product": "pass"}
    assert exported["tries"][0]["judge_saw"] == []
    page = grading_page.page(tmp_path)
    assert "judge: pass" in page
    for retired in ("real_motion", "sudden_change", "does_what_was_ordered", "unreal motion"):
        assert retired not in page


@pytest.mark.parametrize("draw_failed_picture", [False, True])
def test_film_without_a_checked_picture_is_refused_without_paying(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    draw_failed_picture: bool,
) -> None:
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    failure = {**PICTURE_PASSES, "product_same": {"passes": False, "says": "A square rim."}}
    fake_model.respond("check_scene_picture", failure)
    fake_model.respond(
        "scene_agent",
        *([turn(calls=[("draw_starting_picture", PICTURE_FORM)])] if draw_failed_picture else []),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "Need a picture."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    assert "starting picture has passed" in str(results_of("film"))
    assert "make_broll_clip" not in paid_for()


def test_checked_picture_is_the_only_h3_input_and_judge_sees_its_sources(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .conftest import handoffs
    from .test_scene_pictures import PICTURE_FORM

    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "Should have passed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    sent = handoffs("make_broll_clip")
    assert len(sent) == 1, results_of("film")
    assert sent[0]["starting_picture"]
    assert sent[0]["example_pictures"] == []
    assert sent[0].get("reference_videos", []) == []
    assert sent[0]["prompt_enhancement"] is False
    assert "passed" in str(handoffs("produce"))
    from gateway.models import ModelCall
    from jobs.models import ProducedItem

    picture = ProducedItem.objects.get(kind="starting_picture", scene__number=2)
    assert picture.sent is not None
    expected = [
        picture.file,
        *[p["file"] for p in picture.sent["photos"] if p.get("library_number") is not None],
    ]
    judge = ModelCall.objects.filter(purpose="check_clip_product").first()
    assert judge is not None
    assert [image["key"] for image in judge.images[: len(expected)]] == expected
    assert len(judge.images) > len(expected)


def test_failed_clip_is_fixed_in_the_same_scene_chat(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .conftest import handoffs
    from .test_producer_auto_fix import FLOATS, failing
    from .test_scene_pictures import PICTURE_FORM

    fake_model.respond("check_clip_product", failing(FLOATS))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        # The judge failed the clip, so its fix films a new starting picture.
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", {"form": {**FILM["form"], "camera": "The phone tracks the hand"}})]),
        turn(calls=[("give_up", {"reason": "Should have passed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    assert paid_for().count("make_broll_clip") == 2, results_of("film")
    assert "fails" in str(results_of("film")[0]).lower()
    assert "passes" in str(results_of("film")[1]).lower()
    requests = handoffs("scene_agent")
    assert FLOATS in str(requests[-1])
    assert "passed" in str(handoffs("produce"))


def test_worker_restart_collects_the_in_flight_clip_without_paying_twice(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from agents import tasks

    from .conftest import WorkerStopped, handoffs
    from .test_scene_pictures import PICTURE_FORM

    fake_model.respond("collect_clip", WorkerStopped())
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
    with pytest.raises(WorkerStopped):
        steps.run_held()
    assert paid_for().count("make_broll_clip") == 1
    fake_model.respond("produce", turn(says="The scene is ready."))
    tasks.carry_on_after_the_worker_starts()
    steps.run_held()
    assert paid_for().count("make_broll_clip") == 1
    assert "passed" in str(handoffs("produce"))


@pytest.mark.parametrize("checks_on", [True, False])
@pytest.mark.parametrize("box", ["camera", "delivery", "feeling", "product_move", "sounds"])
def test_quality_switch_and_quoted_video_words_refusal_apply_before_paid_film(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    settings: Any,
    checks_on: bool,
    box: str,
) -> None:
    from .test_scene_pictures import PICTURE_FORM

    settings.QUALITY_CHECKS = checks_on
    bad_form = {"form": {**FILM["form"], box: 'At the word "today", she smiles'}}
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", bad_form)]),
        turn(calls=[("film", FILM)]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    assert "video form has quoted text" in str(results_of("film")[0]).lower()
    assert paid_for().count("make_broll_clip") == 1
    assert ("check_clip_motion" in paid_for()) is checks_on
    assert ("check_scene_picture" in paid_for()) is checks_on


@pytest.mark.parametrize("checks_on", [True, False])
def test_positive_video_form_with_short_no_x_and_a_counted_beat_is_filmed(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    settings: Any,
    checks_on: bool,
) -> None:
    from .conftest import handoffs
    from .test_scene_pictures import PICTURE_FORM

    settings.QUALITY_CHECKS = checks_on
    form = {
        "form": {
            **FILM["form"],
            "beats": ["The hand wipes the mug", "After the third wipe, the hand settles"],
            "shows_up_later": "The surface is clear, with no streak or edge left",
        }
    }
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", form)]),
        turn(calls=[("give_up", {"reason": "Should have passed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the wipe.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    filmed = handoffs("make_broll_clip")
    assert len(filmed) == 1, results_of("film")
    assert "no streak or edge left" in filmed[0]["prompt"]
    assert "after the third wipe" in filmed[0]["prompt"]
    assert "passed" in str(handoffs("produce"))


@pytest.mark.parametrize("at_cap", [False, True])
def test_failed_filmed_tries_stay_available_when_the_run_ends(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: Any,
    session_id: str,
    at_cap: bool,
) -> None:
    from jobs import work
    from jobs.models import SceneStep

    from .conftest import handoffs, recorded
    from .test_producer_auto_fix import FLOATS, failing
    from .test_scene_pictures import PICTURE_FORM

    count = 6 if at_cap else 1
    fake_model.respond("check_clip_product", *[failing(FLOATS) for _ in range(count)])
    fake_model.respond(
        "scene_agent",
        *[
            turn(calls=[call])
            for _ in range(count)
            for call in (("draw_starting_picture", PICTURE_FORM), ("film", FILM))
        ],
        turn(calls=[("give_up", {"reason": "The action cannot be filmed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    assert paid_for().count("make_broll_clip") == count, results_of("film")
    ended = SceneStep.objects.get(kind="scene_agent")
    clips = work.clip_tries(ended)
    assert len(clips) == count
    assert "gave up" in str(handoffs("produce"))
    visible = recorded(api, session_id)
    # A B-roll scene's six used clips leave its run open for the ladder's talking step.
    assert ("This run's six clips are used" in results_of("film")[-1]) is at_cap
    assert any("last clip stays" in text for kind, text in visible)


@pytest.mark.parametrize("end_pictures", [0, 1])
def test_a_talking_scene_films_its_named_closest_picture_after_six_fails_with_a_notice(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: Any,
    session_id: str,
    end_pictures: int,
) -> None:
    from gateway.types import Turn
    from jobs.models import ProducedItem

    from .conftest import handoffs, recorded
    from .test_scene_end_pictures import END_PICTURE
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    failed = {**PICTURE_PASSES, "physics": {"passes": False, "says": "The object floats."}}
    fake_model.answer_unscripted("check_scene_picture", lambda _: failed)
    selected: list[int] = []

    def closest() -> Turn:
        picture = ProducedItem.objects.filter(kind="starting_picture", scene__number=3).earliest(
            "version"
        )
        selected.append(picture.pk)
        return turn(
            calls=[
                (
                    "film",
                    {
                        **FILM,
                        "closest_picture": picture.pk,
                        "reason": "Its shape is the closest to the photos.",
                    },
                )
            ]
        )

    fake_model.respond(
        "scene_agent",
        *[turn(calls=[("draw_starting_picture", PICTURE_FORM)]) for _ in range(6)],
        *[turn(calls=[("make_end_picture", END_PICTURE)]) for _ in range(end_pictures)],
        closest,
        turn(calls=[("give_up", {"reason": "This clip should have passed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 3, "instruction": "Show use.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    clip = ProducedItem.objects.get(kind="clip", scene__number=3)
    assert clip.picture_id == selected[0]
    assert paid_for().count("draw_scene_picture") == 6
    assert paid_for().count("make_broll_clip") == 1
    assert "check_clip_product" in paid_for()
    notices = [text for _, text in recorded(api, session_id) if "closest failed picture" in text]
    assert len(notices) == 1
    # The ad goes on and the owner has nothing to do, so the chat isn't told.
    chat = api.get(f"/api/sessions/{session_id}/messages/").json()
    assert not any("closest failed picture" in message["text"] for message in chat)
    assert "passed" in str(handoffs("produce"))


def test_a_broll_scene_never_films_a_failed_picture_and_is_told_the_ladder(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: Any,
    session_id: str,
) -> None:
    from gateway.types import Turn
    from jobs.models import ProducedItem

    from .conftest import handoffs, recorded
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    failed = {**PICTURE_PASSES, "physics": {"passes": False, "says": "The object floats."}}
    fake_model.answer_unscripted("check_scene_picture", lambda _: failed)

    def closest() -> Turn:
        picture = ProducedItem.objects.filter(kind="starting_picture", scene__number=2).earliest(
            "version"
        )
        return turn(calls=[("film", {**FILM, "closest_picture": picture.pk, "reason": "Closest."})])

    fake_model.respond(
        "scene_agent",
        *[turn(calls=[("draw_starting_picture", PICTURE_FORM)]) for _ in range(6)],
        closest,
        turn(calls=[("give_up", {"reason": "Every picture failed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show use.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    drawn = results_of("draw_starting_picture")
    # The picture steps and the rule come once, at the run's first failed picture.
    assert ["frame or angle the shot" in each for each in drawn] == [True] + [False] * 5
    assert "never films a failed starting or end picture" in drawn[0]
    assert "starting pictures 1 of 6, end pictures 0 of 6, clips 0 of 6" in drawn[0]
    assert "starting pictures 6 of 6" in drawn[-1]
    assert "change the scene with the ladder" in str(results_of("film"))
    assert "make_broll_clip" not in paid_for()
    assert not any("closest failed picture" in text for _, text in recorded(api, session_id))
    assert "change the scene with the ladder" in str(handoffs("scene_agent")[-1])


@pytest.mark.parametrize("end_pictures", [0, 1])
@pytest.mark.parametrize(
    ("draws", "reason", "unknown", "refusal"),
    [
        (5, "The shape is closest.", False, "all 6 starting pictures failed"),
        (6, "", False, "Say why this failed picture"),
        (6, "The shape is closest.", True, "current run"),
    ],
)
def test_closest_picture_requires_six_failures_reason_and_a_picture_from_this_run(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    draws: int,
    end_pictures: int,
    reason: str,
    unknown: bool,
    refusal: str,
) -> None:
    from gateway.types import Turn
    from jobs.models import ProducedItem

    from .test_scene_end_pictures import END_PICTURE
    from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

    failed = {**PICTURE_PASSES, "physics": {"passes": False, "says": "The object floats."}}
    fake_model.answer_unscripted("check_scene_picture", lambda _: failed)

    def closest() -> Turn:
        picture = ProducedItem.objects.filter(kind="starting_picture", scene__number=3).earliest(
            "version"
        )
        return turn(
            calls=[
                (
                    "film",
                    {
                        **FILM,
                        "closest_picture": picture.pk + (1000 if unknown else 0),
                        "reason": reason,
                    },
                )
            ]
        )

    fake_model.respond(
        "scene_agent",
        *[turn(calls=[("draw_starting_picture", PICTURE_FORM)]) for _ in range(draws)],
        *[turn(calls=[("make_end_picture", END_PICTURE)]) for _ in range(end_pictures)],
        closest,
        turn(calls=[("give_up", {"reason": "Needs another view."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 3, "instruction": "Show use.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    assert refusal in str(results_of("film"))
    assert "make_broll_clip" not in paid_for()


def test_a_refused_h3_render_is_told_to_the_agent_and_kept_in_the_warnings_report(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: Any,
    session_id: str,
) -> None:
    from gateway.types import ClipFailed

    from .conftest import recorded

    fake_model.respond("make_clip", ClipFailed("Creatify rejected the render"))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "H3 would not render it."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    assert "the video model couldn't make the clip" in results_of("film")[-1]
    assert (
        "problem",
        "The scene_agent's film tool failed on scene 2 (ClipFailed: Creatify rejected the "
        "render). The agent was told and carries on.",
    ) in recorded(api, session_id)
    assert not any(
        "film tool failed" in message["text"]
        for message in api.get(f"/api/sessions/{session_id}/messages/").json()
    )


@pytest.mark.parametrize("filmed", [5, 0], ids=["five filmed first", "none filmed"])
def test_a_last_try_whose_render_fails_ends_the_run_by_the_cap(
    fake_model: FakeModel,
    scene_agent_ready: None,  # noqa: F811
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: Any,
    session_id: str,
    filmed: int,
) -> None:
    from gateway.types import ClipFailed, RefusedForMoney
    from jobs.models import AgentFix

    from .conftest import recorded
    from .test_producer_auto_fix import FLOATS, failing

    fake_model.respond("check_clip_product", *[failing(FLOATS) for _ in range(filmed)])
    fake_model.respond(
        "make_clip",
        *[{} for _ in range(filmed)],
        *[ClipFailed("Creatify rejected the render") for _ in range(5 - filmed)],
        # A last render refused for money, not by the video model, ends the run too.
        RefusedForMoney("Creatify is out of credits"),
    )
    fake_model.respond(
        "scene_agent",
        *[
            turn(calls=[call])
            for _ in range(6)
            for call in (("draw_starting_picture", PICTURE_FORM), ("film", FILM))
        ],
        turn(calls=[("film", FILM)]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    # The run ended on the 6th try: the agent wasn't given a 7th turn.
    assert fake_model.left("scene_agent") == 1
    shown = [message["text"] for message in api.get(f"/api/sessions/{session_id}/messages/").json()]
    failed = [text for _, text in recorded(api, session_id) if "film tool failed" in text]
    assert len(failed) == 6 - filmed
    assert "RefusedForMoney: Creatify is out of credits" in failed[-1]
    if filmed:
        # Its tries are the owner's to pick from, as when the 6th try fails its check.
        assert AgentFix.objects.get().ended_by_cap
        assert any(f"None of the {filmed} tries" in text for text in shown)
    else:
        # No clip to pick from: the scene waits for the owner's choice.
        assert not AgentFix.objects.get().ended_by_cap
        assert any(
            "I couldn't make scene 2: the video model couldn't make any of its 6 tries." in text
            and "waits for your choice" in text
            for text in shown
        )
