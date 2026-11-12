"""The stored presenter gender reaches the model boundaries through the owner's chat."""

from collections.abc import Callable

import pytest

from gateway.fake import FakeModel, turn
from jobs.models import Job

from .conftest import HeldSteps, facts_ok, handoffs
from .test_h3_planning import h3_plan, script_plan
from .test_producer_broll import calling, run
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, picture_checked  # noqa: F401


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("gender", ["man", "woman", ""])
def test_presenter_words_reach_the_scene_form_video_and_judge(
    fake_model: FakeModel,
    product_page_url: str,
    say: Callable[..., None],
    steps: HeldSteps,
    picture_checked: None,  # noqa: F811
    gender: str,
) -> None:
    from jobs.presenter_words import presenter_words

    plan = h3_plan()
    plan["plan"]["person_gender"] = gender or "woman"
    plan["plan"]["scenes"][0]["line"] = (
        "She gave me this Stoneware Mug, and I love her taste in things for the kitchen."
    )
    script_plan(fake_model, product_page_url, plan)
    say(product_page_url)
    if not gender:
        Job.objects.update(person_gender="")
    fake_model.respond("fact_check", facts_ok(1, 2, 3))
    calling(fake_model, say, ("create_person", {}), ("run_planning_checks", {}))
    line = plan["plan"]["scenes"][0]["line"]
    fake_model.respond("transcribe_line", {"text": line})
    fake_model.respond(
        "scene_agent",
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
        turn(calls=[("give_up", {"reason": "The clip should pass."})]),
    )
    calling(
        fake_model,
        say,
        ("start_scene_agent", {"scene": 1, "instruction": "Show it.", "quotes": ["go on"]}),
    )
    run(fake_model, steps)

    words = presenter_words(gender)
    from agents.scene_agent.instructions import INSTRUCTIONS
    from agents.scene_forms import LIP_SYNC_LINE, VideoForm
    from jobs.checks import FACT_CHECK_INSTRUCTIONS
    from jobs.clip_judge import TALKING_SCENE_CONTEXT
    from jobs.plan_quality import PLAN_QUALITY_INSTRUCTIONS
    from jobs.plan_rules import STYLE_LOOKS
    from jobs.scene_rules import EVERY_SCENE_RULES

    quality = next(r for r in fake_model.asked if r.purpose == "check_plan_quality")
    assert quality.instructions == presenter_words(gender or "woman").render(
        PLAN_QUALITY_INSTRUCTIONS
    )
    facts = next(r for r in fake_model.asked if r.purpose == "fact_check")
    assert facts.instructions == words.render(FACT_CHECK_INSTRUCTIONS)
    (video,) = handoffs("make_broll_clip")
    assert words.render(LIP_SYNC_LINE) in video["prompt"]
    assert line in video["prompt"]
    request = next(r for r in fake_model.turns_taken if r.purpose == "scene_agent")
    assert words.render(INSTRUCTIONS) in request.instructions
    assert all(words.render(rule) in request.instructions for rule in EVERY_SCENE_RULES)
    draw = handoffs("draw_scene_picture")[0]
    assert words.render(STYLE_LOOKS["selfie"]) in draw["prompt"]
    film = next(tool for tool in request.tools if tool.name == "film")
    schema = film.arguments.model_json_schema()["$defs"]["VideoForm"]
    assert schema["description"] == words.render(VideoForm.__doc__ or "")
    for check in fake_model.asked:
        if check.purpose.startswith("check_clip_"):
            assert words.render(TALKING_SCENE_CONTEXT) in check.instructions
    assert any(r.purpose == "check_clip_product" for r in fake_model.asked)


def test_mans_words_render_authored_grammar_without_changing_the_template() -> None:
    from jobs.presenter_words import presenter_words

    template = (
        "She holds her cup close to her, then tell her to look at the woman herself. "
        "The phone is held by her at eye level, with her using it on herself. "
        "A result on her where it touched; a result on her that her free hand shows. "
        "The curtain behind her sways. Go to her with it. Her, at the desk."
    )
    words = presenter_words("man")
    assert (words.subject, words.object, words.possessive, words.person) == (
        "he",
        "him",
        "his",
        "the man",
    )
    assert words.render(template) == (
        "He holds his cup close to him, then tell him to look at the man himself. "
        "The phone is held by him at eye level, with him using it on himself. "
        "A result on him where it touched; a result on him that his free hand shows. "
        "The curtain behind him sways. Go to him with it. Him, at the desk."
    )
    assert presenter_words("woman").render(template) == template
    assert presenter_words("").render(template) == template
