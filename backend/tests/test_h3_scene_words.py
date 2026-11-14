"""Transcripts time script captions without judging what H3 said."""

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
LINE = "This handmade mug costs only $10 and holds warm tea every single day."
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


@pytest.mark.parametrize("checks_on", [True, False])
def test_quality_off_still_transcribes_and_no_word_check_runs(
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
    assert checks == []
    assert "Word check" not in str(results_of("film"))


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
        turn(calls=[("draw_starting_picture", TALKING_PICTURE)]),
        turn(calls=[("film", FILM)]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 2, "instruction": "Show the pour.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)
    films = results_of("film")
    assert "It fails." in films[0]
    assert FLOATS in films[0]
    assert "It passes." in films[1]
    assert "Word check" not in str(films)
    assert paid_for().count("make_broll_clip") == 2


@pytest.mark.parametrize("transcribed", [True, False])
@pytest.mark.without_end_card_rules
def test_wrong_words_pass_and_finished_captions_show_the_script(
    fake_model: FakeModel,
    product_page_url: str,
    picture_checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
    transcribed: bool,
) -> None:
    from gateway.types import UnusableReply
    from jobs.models import ProducedItem

    from .conftest import NO_CHOICES, chat, facts_ok
    from .test_h3_planning import h3_plan, script_plan

    plan = h3_plan()
    lines = [
        "Meet the Stoneware Mug from Kiln & Co, and rinse away the gunk after tea.",
        "This mug holds warm tea for you to enjoy at home every day.",
        "Get your Stoneware Mug today from our shop and enjoy your favourite drink.",
    ]
    for scene, line in zip(plan["plan"]["scenes"], lines, strict=True):
        scene.update(line=line)
        if scene["kind"] == "talking":
            scene.update(kind_why="", person_shown="has face")
    script_plan(fake_model, product_page_url, plan)
    say(product_page_url)
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    calling(fake_model, say, ("create_person", {}), ("run_planning_checks", NO_CHOICES))
    heard = lines[0].replace("gunk", "junk")
    fake_model.respond(
        "transcribe_line",
        {"text": heard}
        if transcribed
        else UnusableReply("No answer", input_tokens=0, output_tokens=0),
        *[{"text": text} for text in lines[1:]],
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
    assert len(ad.cuts) == 3
    assert captions == " ".join(lines if transcribed else lines[1:])
    cards = api.get(f"/api/sessions/{session_id}/scenes/").json()
    assert all(card["versions"][-1]["in_ad"] for card in cards)
    assert all(card["versions"][-1]["verdict"] == "pass" for card in cards)
    if not transcribed:
        assert any("could not be transcribed" in text for _, text in recorded(api, session_id))
        assert cards[0]["versions"][-1]["captions"] == []
    assert handoffs("check_words") == []
    assert all("Word check" not in result for result in results_of("film"))
    assert word_records(api, session_id) == []
    assert not any(
        "please listen" in text.lower() or "spoken words could not be checked" in text.lower()
        for _, text in chat(api, session_id)
    )
    assert not any(
        message.get("watch") for message in api.get(f"/api/sessions/{session_id}/messages/").json()
    )
