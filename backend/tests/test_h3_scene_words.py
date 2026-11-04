"""A scene's spoken words are checked beside the picture and motion through the chat."""

import json
from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from jobs.models import Job, Scene

from .conftest import HeldSteps, handoffs, paid_for, recorded, results_of
from .test_h3_scene_film import h3_agent_ready  # noqa: F401
from .test_producer_broll import calling, run
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
LINE = "This handmade mug costs only $10 and holds warm tea."
TALKING_PICTURE = {
    **PICTURE_FORM,
    "person_and_room": "The presenter at the desk with her face and both hands visible",
    "pose": "She holds the mug beside her face, its handle facing the lens",
    "bodies": "One woman, with her face and both hands visible",
}


def word_records(api: APIClient, session_id: str) -> list[dict[str, Any]]:
    """Word-check results as the developer's recorded notices expose them."""
    return [json.loads(text) for _, text in recorded(api, session_id) if '"settled_by"' in text]


@pytest.fixture
def words_ready(h3_agent_ready: Job) -> Job:  # noqa: F811
    Scene.objects.filter(job=h3_agent_ready, number=2).update(
        line=LINE, kind=Scene.Kind.TALKING, kind_why="", person_shown=Scene.PersonShown.HAS_FACE
    )
    return h3_agent_ready


def test_wrong_words_are_returned_beside_the_judge_and_filmed_again(
    fake_model: FakeModel,
    words_ready: Job,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    wrong = "This handmade mug costs only twenty dollars and holds warm tea."
    fake_model.respond("transcribe_line", {"text": wrong}, {"text": LINE})
    fake_model.respond("check_words", {"verdict": "fail", "reason": "The price changed."})
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", TALKING_PICTURE)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "The second clip should have passed."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    films = results_of("film")
    assert len(films) == 2, films
    assert "It fails." in films[0]
    assert "The judge's verdict: pass" in films[0]
    assert LINE in films[0] and wrong in films[0] and "The price changed." in films[0]
    assert "It passes." in films[1]
    assert paid_for().count("make_broll_clip") == 2
    assert handoffs("check_words") == [
        {"line": LINE, "said": wrong, "product_name": words_ready.product_name}
    ]
    checks = word_records(api, session_id)
    assert [(row["verdict"], row["settled_by"]) for row in checks] == [
        ("fail", "model"),
        ("pass", "code"),
    ]
    assert "passed" in str(handoffs("produce"))


@pytest.mark.parametrize("checks_on", [True, False])
def test_written_numbers_pass_without_a_model_and_quality_off_still_transcribes(
    fake_model: FakeModel,
    words_ready: Job,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    settings: Any,
    checks_on: bool,
) -> None:
    settings.QUALITY_CHECKS = checks_on
    fake_model.respond("transcribe_line", {"text": LINE.replace("$10", "ten dollars")})
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", TALKING_PICTURE)]),
        turn(calls=[("film", FILM)]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    assert "It passes." in str(results_of("film"))
    assert handoffs("check_words") == []
    assert paid_for().count("transcribe_line") == 1
    assert ("check_clip_motion" in paid_for()) is checks_on
    checks = word_records(api, session_id)
    assert [row["verdict"] for row in checks] == (["pass"] if checks_on else [])


@pytest.mark.parametrize("unavailable", ["scribe", "model"])
def test_unavailable_words_keep_the_clip_and_tell_owner_to_listen_without_waiting(
    fake_model: FakeModel,
    words_ready: Job,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    unavailable: str,
) -> None:
    from gateway.types import UnusableReply

    from .conftest import chat

    no_answer = UnusableReply("No answer", input_tokens=0, output_tokens=0)
    fake_model.respond(
        "transcribe_line",
        no_answer if unavailable == "scribe" else {"text": "The price is different."},
    )
    if unavailable == "model":
        fake_model.respond("check_words", no_answer)
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", TALKING_PICTURE)]),
        turn(calls=[("film", FILM)]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    assert "Word check: unchecked" in str(results_of("film"))
    assert "It passes." in str(results_of("film"))
    assert "passed" in str(handoffs("produce"))
    assert "listen to the clip yourself" in str(chat(api, session_id))
    assert "ad goes on without waiting" in str(chat(api, session_id))
    card = next(
        row for row in api.get(f"/api/sessions/{session_id}/scenes/").json() if row["scene"] == 2
    )
    assert card["versions"][-1]["in_ad"] is True
    [notice] = [
        row
        for row in api.get(f"/api/sessions/{session_id}/messages/").json()
        if "listen to the clip yourself" in row["text"]
    ]
    assert notice["watch"] == {"scene": 2, "clip": card["versions"][-1]["clip"]}
    checks = word_records(api, session_id)
    assert [row["verdict"] for row in checks] == ["unchecked"]
    if unavailable == "scribe":
        assert any("could not be transcribed" in text for _, text in recorded(api, session_id))


def test_good_words_do_not_pass_a_clip_the_picture_judge_failed(
    fake_model: FakeModel,
    words_ready: Job,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    from .test_producer_auto_fix import FLOATS, failing

    fake_model.respond("transcribe_line", {"text": LINE}, {"text": LINE})
    fake_model.respond("check_clip_product", failing(FLOATS))
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", TALKING_PICTURE)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("film", FILM)]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    films = results_of("film")
    assert "Word check: pass" in films[0] and "It fails." in films[0]
    assert FLOATS in films[0]
    assert "Word check: pass" in films[1] and "It passes." in films[1]
    assert paid_for().count("make_broll_clip") == 2


def test_six_word_failures_end_the_run_and_offer_every_try(
    fake_model: FakeModel,
    words_ready: Job,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    from .conftest import chat

    fake_model.respond("transcribe_line", *[{"text": "It costs twenty dollars."} for _ in range(6)])
    fake_model.respond(
        "check_words", *[{"verdict": "fail", "reason": "The price changed."} for _ in range(6)]
    )
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", TALKING_PICTURE)]),
        *[turn(calls=[("film", FILM)]) for _ in range(6)],
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    assert paid_for().count("make_broll_clip") == 6
    assert len(results_of("film")) == 6
    assert all("It fails." in result for result in results_of("film"))
    assert "6 tries" in str(chat(api, session_id))
    checks = word_records(api, session_id)
    assert len(checks) == 6 and all(row["verdict"] == "fail" for row in checks)


@pytest.mark.parametrize("rule", ["small_slip", "product_name"])
def test_tolerated_words_pass_once_and_finished_captions_follow_the_rule(
    fake_model: FakeModel,
    product_page_url: str,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    rule: str,
) -> None:
    from jobs.models import ProducedItem

    from .conftest import NO_CHOICES, chat, facts_ok
    from .test_h3_planning import h3_plan, script_plan

    plan = h3_plan()
    lines = [
        "Meet the Stoneware Mug, a product made for your favourite tea at home.",
        "This mug holds warm tea for you to enjoy at home every day.",
        "Get your Stoneware Mug today from our shop and enjoy your favourite drink.",
    ]
    for scene, line in zip(plan["plan"]["scenes"], lines, strict=True):
        scene.update(line=line, kind="talking", kind_why="", person_shown="has face")
    script_plan(fake_model, product_page_url, plan)
    say(product_page_url)
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    calling(fake_model, say, ("create_person", {}), ("run_planning_checks", NO_CHOICES))
    heard = (
        lines[0].replace("product", "products")
        if rule == "small_slip"
        else lines[0].replace("Stoneware", "Stonewear")
    )
    fake_model.respond("transcribe_line", *[{"text": text} for text in [heard, *lines[1:]]])
    fake_model.respond(
        "check_words",
        {
            "verdict": "pass",
            "reason": "A difference a listener would not notice.",
            "rule": rule,
            "name_heard": "Stonewear",
            "name_written": "Stoneware",
        },
    )
    for number in range(1, 4):
        fake_model.respond(
            "scene_agent",
            turn(calls=[("draw_starting_picture", TALKING_PICTURE)]),
            turn(calls=[("film", FILM)]),
        )
        calling(
            fake_model,
            say,
            (
                "start_scene_agent",
                {"scene": number, "instruction": "Show the mug.", "quotes": ["Go on"]},
            ),
        )
        run(fake_model, steps)
    calling(fake_model, say, ("create_music", {"mood": "warm and calm"}), ("assemble_ad", {}))

    assert paid_for().count("make_broll_clip") == 3
    assert all("It passes." in result for result in results_of("film"))
    ad = Job.objects.get().produced.get(kind=ProducedItem.Kind.FINISHED_AD)
    captions = " ".join(caption["text"] for caption in ad.captions)
    expected_line = heard if rule == "small_slip" else lines[0]
    assert captions == " ".join([expected_line, *lines[1:]])
    checks = word_records(api, session_id)
    assert checks[0]["rule"] == rule
    assert checks[0]["caption_line"] == expected_line
    assert not any(
        "slip" in text or "products" in text
        for role, text in chat(api, session_id)
        if role == "notice"
    )
