"""Hair brushes and styling tools (Julian, Nov 10 2026 02:00, thread "Fix hair retest clip
problems"; root causes in round 13's hair retest): a one-sided brush works on her hair with
her face in the shot, bristles into the hair and its back to the camera, or films the result
when no photo shows its back; a tool the hair wraps around never films the wrapping and shows
the after hair instead; and a page's how-to photo is never sent to the picture model, so its
posed hands can't be copied, while the agent still follows its steps in words. Driven through
the chat, outside services faked."""

from collections.abc import Callable

import pytest
from pytest_django import Settings

from adforge import file_store
from gateway.fake import FakeModel
from jobs import work
from jobs.library import PagePhoto, PictureLabel, add_to_library, library_of
from jobs.models import Job
from jobs.picture_rules import HAIR_TOOL_RULE, NO_FACE_RULE

from .conftest import LABELLED, HeldSteps, facts_ok, photo, results_of
from .test_h3_planning import h3_plan, script_plan
from .test_make_picture import (  # noqa: F401 (fixtures)
    a_scene,
    asked,
    draw_for,
    in_the_ads_colour,
    library,
    making,
    prepare_scenes,
)
from .test_picker_labels import labelled_page  # noqa: F401 (a fixture)

pytestmark = [pytest.mark.django_db(transaction=True)]

Say = Callable[..., None]

BRUSHING = "her hand runs the brush down a lock of her hair, bristles in, its back to the lens"


def test_the_hair_rule_says_how_each_kind_of_hair_tool_is_filmed() -> None:
    # One-sided brushes: the face-in-shot rule, held the way it works, else the result.
    for said in (
        "one-sided brush",
        "her face is in the shot",
        "the brush or her hand covers the part of the hair being done right now",
        "the hair it has already passed shows the result",
        "bristles into the hair and its back to the camera",
        "When no page photo shows the brush's back, the scene films the result instead",
        "never the brush turned round",
        "never drops a use on her hair from the plan",
    ):
        assert said in HAIR_TOOL_RULE
    # Tools the hair wraps around: never the wrapping, the after hair clearly different.
    for said in (
        "hair wraps around",
        "never film the wrapping",
        "she holds the product and shows the after effect",
        "clearly different from her hair in earlier scenes",
    ):
        assert said in HAIR_TOOL_RULE
    # A B-roll scene of a one-sided brush on her hair is the one B-roll that shows her face.
    assert "A B-roll scene never shows her face" in NO_FACE_RULE
    assert '("has face")' in NO_FACE_RULE and "one-sided brush" in NO_FACE_RULE


def test_the_hair_rule_is_told_to_the_planner_the_producer_the_scene_agent_and_picture_form() -> (
    None
):
    from agents.producer import INSTRUCTIONS as PRODUCER_INSTRUCTIONS
    from agents.scene_forms import PictureForm
    from jobs.planning import PLAN_INSTRUCTIONS
    from jobs.scene_rules import Place, rules_for

    assert PLAN_INSTRUCTIONS.count(HAIR_TOOL_RULE) == 1
    assert HAIR_TOOL_RULE in (PictureForm.model_fields["pose"].description or "")
    # Every scene kind, everywhere a scene can change after planning: the producer, the
    # scene agent and the fact check's rewrite read the same scene rules.
    for told in (
        rules_for([Place(1, broll=False)], 3),
        rules_for([Place(2, broll=True)], 3),
        PRODUCER_INSTRUCTIONS,
    ):
        assert told.count(HAIR_TOOL_RULE) == 1


def test_a_broll_hair_scene_with_her_face_is_kept_through_the_plan_and_its_fact_rewrite(
    fake_model: FakeModel,
    product_page_url: str,
    say: Say,
    settings: Settings,
) -> None:
    from .test_producer_checks import failing

    plan = h3_plan()
    plan["plan"]["scenes"][1].update(shows=BRUSHING, person_shown="has face")
    script_plan(fake_model, product_page_url, plan, checks=True)
    shows_wrong = failing(2, "The page doesn't say that.", passing=(1, 3))
    shows_wrong["lines"][-1]["wrong"] = "shows"
    fake_model.respond("fact_check", shows_wrong, facts_ok(2))
    rewritten = f"{BRUSHING}, from the ends up"
    fake_model.respond("rewrite_line", {**plan["plan"]["scenes"][1], "shows": rewritten})

    say(product_page_url)

    (rewrite,) = [each for each in fake_model.asked if each.purpose == "rewrite_line"]
    assert HAIR_TOOL_RULE in " ".join(rewrite.instructions.split())
    assert results_of("run_planning_checks")[0].startswith("The checks passed.")
    scene = Job.objects.get().scenes.get(number=2)
    assert (scene.kind, scene.person_shown, scene.shows) == ("broll", "has face", rewritten)
    # Her face is in it, so it is drawn with the presenter in the room, not the room alone.
    assert work.person_shown_for(scene.kind, scene.person_shown) == "has face"
    assert work.scene_place(scene, scene.person_shown)[0] == "person_in_room"


def test_a_broll_scene_turned_from_talking_goes_without_her_face_unless_asked() -> None:
    assert work.person_shown_for("talking") == "has face"
    assert work.person_shown_for("broll") == "no face"
    assert work.person_shown_for("broll", "has face") == "has face"
    assert work.person_shown_for("talking", "no face") == "has face"


@pytest.fixture
def how_to(library: Job) -> int:  # noqa: F811
    """A how-to photo from the page in the ad's library, as the picker labels one: a guide
    with the presenter's hands posed on the product. Gives its library number."""
    file = file_store.save(f"jobs/{library.pk}/how-to.png", photo(71))
    add_to_library(
        library,
        file,
        PictureLabel.model_validate(
            {
                **LABELLED,
                "kind": "how-to",
                "side": "none",
                "alone": "no",
                "label": "A guide: hair laid over the brush's front bristles, step 1 to 3.",
            }
        ),
        PagePhoto(photo=3),
    )
    return library_of(library)[-1].number


def test_the_scene_agent_cant_send_a_how_to_photo_to_the_picture_model(
    fake_model: FakeModel,
    library: Job,  # noqa: F811
    how_to: int,
    say: Say,
    in_the_ads_colour: None,  # noqa: F811
    steps: HeldSteps,
) -> None:
    scene = a_scene(library, 1)
    prepare_scenes(fake_model, say, steps, scene)

    call, drawings = draw_for(fake_model, say, steps, scene, 1, how_to)

    assert call.result.startswith("Refused:")
    assert f"Library item {how_to} is a how-to photo" in call.result
    assert "Follow its steps in words" in call.result
    assert drawings == []
    call, [drawing] = draw_for(fake_model, say, steps, scene, 1)
    assert call.answer == "made"
    how_to_file = library_of(library)[how_to - 1].file
    assert how_to_file not in drawing.handoff["pictures"]


def test_the_producer_cant_make_a_picture_from_a_how_to_photo(
    fake_model: FakeModel,
    library: Job,  # noqa: F811
    how_to: int,
    say: Say,
) -> None:
    call = making(fake_model, say, references=[1, how_to])

    assert call.answer == "cant"
    assert call.result.startswith("Refused:")
    assert f"Library item {how_to} is a how-to photo" in call.result
    assert asked("make_picture") == []
