"""Owner facts, shared-scene changes, and short scripts through the chat (#550)."""

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from gateway.fake import FakeModel, turn
from jobs.models import ProducedItem, Scene

from .conftest import HeldSteps, facts_ok, handoffs, results_of
from .test_producer_broll import checked, run  # noqa: F401
from .test_producer_fixes import shown  # noqa: F401
from .test_producer_plan_edits import owner_says
from .test_scene_agent_changes import change
from .test_scene_film import FILM
from .test_scene_pictures import PICTURE_FORM, PICTURE_PASSES

pytestmark = pytest.mark.django_db(transaction=True)


def scene_script(fake_model: FakeModel, *changes: tuple[str, dict[str, object]]) -> None:
    fake_model.answer_unscripted("check_scene_picture", lambda _: PICTURE_PASSES)
    fake_model.respond(
        "scene_agent",
        *[turn(calls=[changed]) for changed in changes],
        turn(calls=[("draw_starting_picture", PICTURE_FORM)]),
        turn(calls=[("film", FILM)]),
    )


def note(fake_model: FakeModel, say: Callable[..., None], words: str, *, scene: int = 2) -> None:
    owner_says(
        fake_model,
        say,
        words,
        (
            "pass_note",
            {
                "scenes": [
                    {"scene": scene, "instruction": words, "quotes": [words], "owner_asked": True}
                ]
            },
        ),
    )


def test_an_earlier_owner_correction_reaches_the_scene_fact_check_during_a_later_note(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    correction = "The mug holds 500 ml, not the 350 ml on our page."
    fake_model.respond("produce", turn(says="I’ll use your corrected size."))
    say(correction)
    later = "Have scene 2 say the corrected size."
    line = "The mug holds five hundred millilitres of your favourite tea at home."
    scene = Scene.objects.get(number=2)
    scene_script(fake_model, change(line=line, shows=scene.shows))
    fake_model.respond("fact_check", facts_ok(2))
    note(fake_model, say, later)
    run(fake_model, steps)

    assert results_of("change_scene"), results_of("pass_note")
    assert not any(answer.startswith("Refused:") for answer in results_of("change_scene")), (
        results_of("change_scene")
    )
    given = handoffs("fact_check")[-1]
    owner_messages = [entry["text"] for entry in given["conversation"] if entry["by"] == "user"]
    assert correction in owner_messages and later in owner_messages
    assert given["lines"][0]["line"] == line
    assert Scene.objects.get(number=2).line == line
    assert not any(answer.startswith("Refused:") for answer in results_of("change_scene"))
    assert not handoffs("rewrite_line")


def test_a_shared_owner_change_reaches_cohesion_while_the_other_scene_still_has_its_old_action(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    old = "She sets the mug on a wooden workbench."
    owner_says(
        fake_model,
        say,
        "Put the mug on the wooden workbench in scene 3.",
        ("edit_scene", {"scene": 3, "shows": old, "owner_asked": True}),
    )
    words = "In scenes 2 and 3, set the mug on a tile instead of the wooden workbench."
    new = "A hand sets the mug on a tile."
    line = Scene.objects.get(number=2).line
    changed = change(line=line, shows=new)
    scene_script(fake_model, changed, changed)
    fake_model.respond("fact_check", facts_ok(2))
    fake_model.respond("check_scene_cohesion", {"decision": "matches", "reason": "Owner asked."})
    note(fake_model, say, words)
    run(fake_model, steps)

    given = handoffs("check_scene_cohesion")[-1]
    assert words in [
        message["text"] for message in given["conversation"] if message["by"] == "user"
    ]
    assert {
        "number": 3,
        "kind": "talking",
        "line": Scene.objects.get(number=3).line,
        "shows": old,
    } in given["other_scenes"]
    assert Scene.objects.get(number=2).shows == new
    assert Scene.objects.get(number=3).shows == old
    assert not any("doesn't match" in answer for answer in results_of("change_scene"))


@pytest.mark.parametrize("number", [2, 3], ids=["broll", "talking"])
def test_an_exact_owner_short_line_passes_planning_change_and_filming_at_the_shortest_clip(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    number: int,
) -> None:
    from .conftest import NO_CHOICES
    from .test_producer_plan_edits import answer_of

    line = "Meet our mug for everyday morning tea."
    owner_says(
        fake_model,
        say,
        f'Scene {number} must say exactly "{line}".',
        ("edit_scene", {"scene": number, "line": line, "owner_asked": True}),
    )
    fake_model.respond("fact_check", facts_ok(number))
    owner_says(fake_model, say, "Check that line.", ("run_planning_checks", NO_CHOICES))
    assert answer_of("run_planning_checks").startswith("The checks passed."), answer_of(
        "run_planning_checks"
    )
    assert not handoffs("lengthen_line")
    scene = Scene.objects.get(number=number)
    scene_script(fake_model, change(line=line, shows=scene.shows, person_shown=scene.person_shown))
    note(fake_model, say, "Film that exact line.", scene=number)
    run(fake_model, steps)

    assert Scene.objects.get(number=number).line == line
    assert handoffs("make_broll_clip")[-1]["seconds"] == 5
    assert f"<d>[English] {line}</d>" in handoffs("make_broll_clip")[-1]["prompt"]
    assert not any(answer.startswith("Refused:") for answer in results_of("change_scene"))
    assert not any(answer.startswith("Refused:") for answer in results_of("film"))


def test_default_shortening_keeps_an_exact_owner_short_line_instead_of_lengthening_it(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Callable[..., None],
) -> None:
    from .conftest import NO_CHOICES
    from .test_producer_plan_edits import AS_BROLL, answer_of

    own = "Meet our mug for everyday morning tea."
    long = " ".join(["A mug from Kiln & Co for your favourite drink at home."] * 2)
    normal = "A mug from Kiln & Co for your favourite drink at home."
    owner_says(
        fake_model,
        say,
        f'Scene 2 says exactly "{own}". Add another product view and expand the other lines.',
        ("edit_scene", {"scene": 1, "line": long, "owner_asked": True}),
        ("edit_scene", {"scene": 2, "line": own, "owner_asked": True}),
        ("edit_scene", {"scene": 3, "line": long, "owner_asked": True}),
        (
            "add_scene",
            {
                "at": 4,
                "line": long,
                "shows": "The mug rests on a display shelf.",
                **AS_BROLL,
                "owner_asked": True,
            },
        ),
    )
    # A first response tries lengthening the owner's words to the normal minimum.
    # It must be rejected; the following response preserves the exact short line.
    fake_model.respond(
        "shorten_script",
        {"lines": [{"scene": number, "line": normal} for number in range(1, 5)]},
        {
            "lines": [
                {"scene": number, "line": own if number == 2 else normal} for number in range(1, 5)
            ]
        },
    )
    fake_model.answer_unscripted(
        "fact_check", lambda request: facts_ok(*(line.scene for line in request.handoff.lines))
    )
    owner_says(fake_model, say, "Check the expanded ad.", ("run_planning_checks", NO_CHOICES))
    assert answer_of("run_planning_checks").startswith("The checks passed."), answer_of(
        "run_planning_checks"
    )
    assert Scene.objects.get(number=2).line == own
    assert not handoffs("lengthen_line")


def test_a_short_line_the_app_wrote_after_the_first_ad_still_gets_lengthened(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Callable[..., None],
) -> None:
    from .conftest import NO_CHOICES

    app_line = "A lovely mug for your morning tea."
    fitted = "This lovely mug holds your favourite hot tea at home every morning."
    owner_says(
        fake_model,
        say,
        "Make scene 2 more welcoming.",
        ("edit_scene", {"scene": 2, "line": app_line, "owner_asked": True}),
    )
    fake_model.respond("fact_check", facts_ok(2), facts_ok(2))
    fake_model.respond("lengthen_line", {"line": fitted})
    owner_says(fake_model, say, "Check it.", ("run_planning_checks", NO_CHOICES))
    assert handoffs("lengthen_line")[-1]["script"][1]["line"] == app_line
    assert Scene.objects.get(number=2).line == fitted


def test_a_producer_clip_limit_rewrite_keeps_the_owner_script_and_explains_the_change(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    from .conftest import NO_CHOICES, chat
    from .test_producer_checks import failing
    from .test_producer_plan_edits import answer_of

    original = " ".join(["Our mug holds your tea"] * 12) + "."
    replacement = "This mug costs nineteen dollars and holds your favourite morning tea."
    corrected = "This mug costs twenty four dollars and holds your favourite morning tea."
    reason = "your script is longer than the video model can speak in one 15-second clip."
    owner_says(
        fake_model,
        say,
        f'Scene 2 must say exactly "{original}".',
        ("edit_scene", {"scene": 2, "line": original, "owner_asked": True}),
    )
    owner_says(
        fake_model,
        say,
        "Fit it into one clip while keeping my point.",
        ("edit_scene", {"scene": 2, "line": replacement, "owner_asked": True}),
        (
            "edit_scene",
            {"scene": 2, "line": replacement, "owner_asked": True, "line_change_reason": reason},
        ),
    )
    assert results_of("edit_scene")[-2].startswith("Refused:")
    assert "line_change_reason" in results_of("edit_scene")[-2]
    assert not answer_of("edit_scene").startswith("Refused:"), answer_of("edit_scene")
    visible = " ".join(text for _, text in chat(api, session_id))
    assert replacement in visible and reason in visible
    fake_model.respond(
        "fact_check", failing(2, "The generated price contradicts the page."), facts_ok(2)
    )
    fake_model.respond(
        "rewrite_line",
        {
            "line": corrected,
            "shows": Scene.objects.get(number=2).shows,
            "broll_kind": "showcase",
            "person_shown": "no face",
        },
    )
    owner_says(fake_model, say, "Check it.", ("run_planning_checks", NO_CHOICES))
    assert original in handoffs("rewrite_line")[-1]["owner_words"]


def test_a_checked_new_owner_script_replaces_the_old_script_without_a_rewrite_notice(
    fake_model: FakeModel,
    shown: ProducedItem,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
    api: APIClient,
    session_id: str,
) -> None:
    from .conftest import NO_CHOICES, chat

    old = "Our mug holds your tea for happy mornings at home every single day."
    new = "Meet our mug for everyday morning tea."
    for line in (old, new):
        owner_says(
            fake_model,
            say,
            f'Scene 2 must say exactly "{line}".',
            ("edit_scene", {"scene": 2, "line": line, "owner_asked": True}),
        )
    fake_model.respond("fact_check", facts_ok(2))
    owner_says(fake_model, say, "Check my new line.", ("run_planning_checks", NO_CHOICES))
    scene_script(fake_model)
    note(fake_model, say, "Film the new words.")
    run(fake_model, steps)
    assert new in str(handoffs("scene_agent")[-1])
    assert old not in str(handoffs("scene_agent")[-1])
    assert not any("I changed" in text or "I left out" in text for _, text in chat(api, session_id))
