"""The planner's schema, shared rules and accepted and refused plans. No chat and no
database: the plan is read as the gateway reads it, with `producer_decision_for`, and the
chat's own tests in test_producer_plan.py prove the instructions and a refused plan reach
and leave it the same way."""

import json
import re
from typing import Any

import pytest
from pydantic import ValidationError

from jobs.plan_rules import (
    AT_LEAST_ONE_BROLL,
    BOX_NEEDS_PACKAGING,
    DIFFERENT_STYLE_AND_STATE,
    LENS_FIRST_OR_LAST,
    MIDDLE_IS_BROLL,
    RULES,
    STYLE_AND_STATE,
    told_rules_broken,
)
from jobs.planning import (
    NOT_A_CLAIM,
    PLAN_INSTRUCTIONS,
    SPREAD_ON_BODY_RULE,
    producer_decision_for,
)

from . import conftest
from .conftest import PLAN, broll, styled

# The decision for a job, as the planner is asked for it: its library holds 12 pictures,
# and its one page video was watched into 2 moments.
LIBRARY = range(1, 13)
MOMENTS = {1: 2}
DECISION = producer_decision_for(library=LIBRARY, moments=MOMENTS)
# What the planner is told of each field, as its schema is given to the model.
SCHEMA = DECISION.model_json_schema()["$defs"]


def a_plan_with(**changes: Any) -> dict[str, Any]:
    """PLAN with some of its plan's fields replaced, its talking scenes given talking styles
    and product states that keep the whole-plan rules, for tests about other rules."""
    if "scenes" in changes:
        changes["scenes"] = styled(changes["scenes"])
    return conftest.a_plan_with(**changes)


def opening(**changes: Any) -> dict[str, Any]:
    return {
        "kind": "talking",
        "line": "Meet the Stoneware Mug for your favourite warm drinks at home.",
        "shows": "The presenter shows the mug to the lens",
        **changes,
    }


def closing() -> dict[str, Any]:
    return {
        "kind": "talking",
        "line": "Order yours today for $24.00 from the shop for home.",
        "shows": "The presenter displays the mug's handle beside her face",
    }


def broll_scene(**labels: Any) -> dict[str, Any]:
    """A close view of the mug in an otherwise talking, three-scene plan."""
    scene = broll(
        {
            "line": "The mug holds 350 ml of your favourite hot drink.",
            "shows": "Hot tea pours into the mug",
            **labels,
        }
    )
    return a_plan_with(scenes=[opening(), scene, closing()])


def talking_scene(**labels: Any) -> dict[str, Any]:
    """A presenter demonstrates the mug in the second scene, talking while doing it, with a
    B-roll scene before the call to action."""
    return a_plan_with(
        scenes=[
            opening(),
            {
                "kind": "talking",
                "line": "The mug holds 350 ml of your favourite hot drink.",
                "shows": "The presenter drinks from the mug",
                **labels,
            },
            broll(
                {
                    "line": "Every mug is hand-thrown, so each one is its own.",
                    "shows": "Hot tea pours into the mug",
                }
            ),
            closing(),
        ]
    )


def a_plan_without(field: str) -> dict[str, Any]:
    """PLAN with one of its plan's fields left out altogether."""
    return {**PLAN, "plan": {name: value for name, value in PLAN["plan"].items() if name != field}}


def a_plan_with_ways(*kinds: str) -> dict[str, Any]:
    """PLAN with a scene of each kind after its opening line: "talking" to camera,
    "broll" showing the mug, "second state" showing the second of two end states, or
    "talking second state", said to camera yet given as a second state."""
    scenes: list[dict[str, Any]] = [opening()]
    for number, kind in enumerate(kinds, start=2):
        line = {"line": "It holds 350 ml of hot tea or cold brew."}
        if kind == "talking":
            scenes.append(
                {
                    "kind": "talking",
                    "shows": f"The presenter drinks in scene {number}",
                    **line,
                }
            )
        elif kind == "talking second state":
            scenes.append(
                {
                    "kind": "talking",
                    "shows": f"The presenter drinks in scene {number}",
                    **line,
                    "second_state": True,
                }
            )
        else:
            # Each B-roll scene films an action of its own.
            scenes.append(broll({**line, "shows": f"the mug in a hand, seen in scene {number}"}))
            scenes[-1]["second_state"] = kind == "second state"
    # This fixture includes a talking closing scene when none was supplied.
    if len(scenes) < 3 or scenes[-1]["kind"] != "talking":
        scenes.append(closing())
    return a_plan_with(scenes=scenes)


# --- What the planner is told ----------------------------------------------------------------


def test_each_scene_pictures_purpose_is_described_with_exactly_the_values_it_may_take() -> None:
    # The `for` values are not approved yet (audit item 11): when they change, what the
    # planner reads of the field changes with them.
    purpose = SCHEMA["ScenePicture"]["properties"]["for"]
    assert re.findall(r'"([^"]+)"', purpose["description"]) == purpose["enum"]


def test_broll_scene_schema_has_its_action_and_reference_fields() -> None:
    scene = SCHEMA["H3PlannedScene"]["properties"]
    assert set(scene) >= {"broll_kind", "person_shown", "usage", "result", "needs"}


def test_the_planner_is_told_the_shape_of_every_script() -> None:
    instructions = PLAN_INSTRUCTIONS
    # Creatify's own words, research/creatify-script-shape-originals.md in the project files:
    # "The first 3 seconds determine whether someone watches your ad.", "Match the structure
    # to your goal", "Pick one. Don't mix frameworks in the same video.", "One ad, one idea."
    # and "'Learn more' is not a CTA."
    for script_format, parts in [
        ("problem, agitate, solve", "the problem, agitate, solve"),
        ("feature cascade", "the hero feature, a supporting feature, proof"),
        ("before and after", "the before state, the transformation moment, the after state"),
        ("day in the life", "the routine, the key moment, the result"),
    ]:
        assert f'- "{script_format}": {parts}' in instructions
    assert "part" in SCHEMA["H3PlannedScene"]["properties"]
    assert "script_format" in SCHEMA["Plan"]["properties"]


def test_the_planner_is_told_every_broll_scene_does_something_that_sells_it() -> None:
    # Round 2: every "showcase" (a hand holding, setting down or standing up the product)
    # failed: #12 s2, #8 s4, N3 s2-5. The bag carried as a clutch and worn crossbody was graded
    # PERFECT (#5, decisions/2026-10-07-broll-prompts.md). Julian's round 2 rule "B-roll must
    # sell: every clip does something" is back in place of the proof rule (#437, Nov 7 2026),
    # still allowing a still shot whose line is about exactly where it fits or how it looks.

    kind = SCHEMA["H3PlannedScene"]["properties"]["broll_kind"]
    assert '"showcase" when unsure' not in kind["description"]
    assert "such as a bag carried as a clutch" in kind["description"]


def test_the_hooks_problem_and_her_opinion_need_no_source() -> None:
    # Round 12 (Julian, Nov 6): every hook was page copy, flat. The viewer's problem and the
    # person's opinion aren't product claims, for the planner, the fact check and its rewrite
    # alike. What the hook says and how long it is: tests/test_hook_sells.py.
    from jobs.checks import FACT_CHECK_INSTRUCTIONS
    from jobs.planning import HOOK_WORDS
    from jobs.scene_rules import Place, rules_for

    for told in (PLAN_INSTRUCTIONS, FACT_CHECK_INSTRUCTIONS):
        assert NOT_A_CLAIM in told
    # A rewritten or lengthened hook keeps its length: the first scene's rules say so.
    assert HOOK_WORDS in rules_for([Place(1, broll=False)], 3)


def test_every_line_writer_keeps_the_shared_spoken_style() -> None:
    from agents.producer import INSTRUCTIONS as PRODUCER_INSTRUCTIONS
    from agents.scene_agent.instructions import INSTRUCTIONS as SCENE_INSTRUCTIONS
    from jobs.checks import (
        LENGTHEN_LINE_INSTRUCTIONS,
        SHORTEN_INSTRUCTIONS,
        SHORTEN_LINE_INSTRUCTIONS,
    )
    from jobs.planning import SPOKEN_LINE_RULE

    for instructions in (
        PLAN_INSTRUCTIONS,
        SHORTEN_LINE_INSTRUCTIONS,
        LENGTHEN_LINE_INSTRUCTIONS,
        SHORTEN_INSTRUCTIONS,
        PRODUCER_INSTRUCTIONS,
        SCENE_INSTRUCTIONS,
    ):
        assert SPOKEN_LINE_RULE in instructions


def test_the_planner_films_a_skin_product_applied_only_when_a_page_photo_shows_it() -> None:
    # Root cause 4 part 1 (Julian, Nov 9 2026): applying a cream or foam never passed (Glow
    # Recipe R6, Krave R8-R10, Bondi R12-R13); the result where it touched and a fingertip dab
    # passed his Nov 10 test.
    assert SPREAD_ON_BODY_RULE in PLAN_INSTRUCTIONS
    # Every scene kind, everywhere a scene can change (Julian, Nov 10 2026 00:13).
    from agents.producer import INSTRUCTIONS as PRODUCER_INSTRUCTIONS
    from jobs.scene_rules import Place, rules_for

    for told in (
        rules_for([Place(1, broll=False)], 3),
        rules_for([Place(3, broll=False)], 3),
        rules_for([Place(2, broll=True)], 3),
        PRODUCER_INSTRUCTIONS,
    ):
        assert told.count(SPREAD_ON_BODY_RULE) == 1
    # A middle talking scene "talking while doing it" is that talking scene: its looks (drawn
    # into its starting picture), the middle-scene rule and the producer's give-up offer.
    from jobs.plan_rules import DOING_IT, MIDDLE_IS_BROLL, SPREAD_WHILE_TALKING, STYLE_LOOKS

    for told in (STYLE_LOOKS[DOING_IT], MIDDLE_IS_BROLL, PLAN_INSTRUCTIONS):
        assert SPREAD_WHILE_TALKING in told


def test_the_planner_is_told_a_scene_needs_only_how_a_thing_looks() -> None:
    # Round 2 #12 s5: its need was "the edge-first drop", photo 2, the shop's drop photo; the
    # start picture copied its low hand, shoe and camera height. Julian 03:58: the picture
    # model copies the photo whatever its job says.
    needs = SCHEMA["H3PlannedScene"]["properties"]["needs"]
    assert "only how a thing looks" in needs["description"]


def test_the_planner_offers_three_answers_for_something_of_the_product_no_photo_shows() -> None:
    # Owner part loop (spec handoffs/owner-part-approval, Julian Oct 19, Q6 "the ask offers
    # all three"): most shop owners have no photo of such a part, and "go ahead without it"
    # lost every scene that needed it. They can now describe it or have it drawn, and approve
    # the picture before its video is made.
    owner_part = SCHEMA["H3PlannedScene"]["properties"]["owner_part"]
    assert "described it or asked you to draw it" in owner_part["description"]
    part = SCHEMA["OwnerPart"]["properties"]
    assert part["source"]["enum"] == ["owner's words", "invented"]
    assert "their sentence as they said it" in part["looks"]["description"]


def test_the_planner_is_told_a_line_naming_the_problem_shows_the_problem() -> None:
    # Test ads (08 Oct), N3: the "builds up" line was talking; Julian: "showing like how the
    # showers can get dirty over time. As a B-roll scene." Creatify: "Before state (3-5s):
    # Show the problem visually".
    scene = SCHEMA["H3PlannedScene"]["properties"]
    assert "shows the problem" in json.dumps(scene["broll_kind"])
    assert scene["thin_film"]["description"] == (
        "True when the problem this scene shows, or the result it ends on, is a thin film, "
        "haze, cloudiness or water spots on a surface."
    )


def test_the_planner_is_told_to_copy_the_products_name_word_for_word_from_a_line() -> None:
    # A page's title often has more in it than anyone says, such as "Anker 313 Power Bank
    # (PowerCore 10K)" or "hydro-stars® + big yellow". Given as the name, it's in no line, so
    # the plan is refused and paid for again: 9 of the first 122 plans were.
    assert SCHEMA["Plan"]["properties"]["product_name"]["description"] == (
        "The product's name copied word for word from a line, exactly as the person says it "
        "there: not the page's full title."
    )


# --- What is read from the plan ---------------------------------------------------------------


def test_both_scene_kinds_keep_their_actions_with_whitespace_tidied() -> None:
    scenes = [
        opening(shows="  The presenter shows   the mug to the lens. "),
        broll(
            {
                "line": "The mug holds your favourite hot drink for cosy mornings.",
                "shows": "  Hot tea   pours into the mug. ",
            }
        ),
        closing(),
    ]
    decision = DECISION.model_validate(a_plan_with(scenes=scenes))
    assert decision.plan is not None
    assert [(scene.kind, scene.shows) for scene in decision.plan.scenes[:2]] == [
        ("talking", "The presenter shows the mug to the lens."),
        ("broll", "Hot tea pours into the mug."),
    ]


def test_a_broll_scenes_blank_usage_and_result_are_read_as_empty() -> None:
    decision = DECISION.model_validate(broll_scene(usage="  ", result=""))

    assert decision.plan is not None
    assert decision.plan.scenes[1].broll_details() == {
        "broll_kind": "showcase",
        "person_shown": "no face",
        "usage": "",
        "result": "",
        "needs": [],
    }


# --- A plan that can't be used ----------------------------------------------------------------


# What the shop owner said a part no photo shows looks like, and the part as the planner
# gives it from their words.
THEIR_SENTENCE = "A pale wooden handle with a round brass cap."
OWNER_PART = {"what": "the handle", "source": "owner's words", "looks": THEIR_SENTENCE}


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(PLAN, id="three talking scenes"),
        pytest.param(broll_scene(), id="a showcase"),
        pytest.param(broll_scene(owner_part=OWNER_PART), id="an owner part from their words"),
        pytest.param(
            broll_scene(owner_part={**OWNER_PART, "source": "invented"}),
            id="an owner part the planner invents",
        ),
        pytest.param(talking_scene(), id="a second talking scene"),
        pytest.param(talking_scene(owner_part=OWNER_PART), id="a talking scene with an owner part"),
        pytest.param(
            talking_scene(needs=[{"what": "the handle", "photos": [1]}]),
            id="a talking scene with needs",
        ),
        pytest.param(
            talking_scene(scene_pictures=[{"number": 4, "for": "person"}]),
            id="a talking scene with drawing photos",
        ),
        pytest.param(
            talking_scene(piece={"video": 1, "moment": 1}), id="a talking scene with a video piece"
        ),
        pytest.param(a_plan_with_ways("broll", "second state"), id="two end states"),
        pytest.param(
            # The graded Creatify ads opened and closed with her showing the product to the
            # lens (docs/agents/proven-broll-rules.md): talking scenes may share an action.
            a_plan_with(
                scenes=[
                    opening(),
                    broll(
                        {
                            "line": "It holds 350 ml for your favourite hot drink at home.",
                            "shows": "Hot tea pours into the mug",
                        }
                    ),
                    {**closing(), "shows": "The presenter shows THE mug to the lens."},
                ]
            ),
            id="the hook and the call to action showing the product to the lens",
        ),
        # Drawing suggestions remain unrestricted by the old five-picture cap.
        pytest.param(
            broll_scene(
                person_shown="no face",
                needs=[{"what": f"part {n}", "photos": [1]} for n in range(1, 4)],
            ),
            id="a scene suggesting several drawing photos",
        ),
        pytest.param(
            broll_scene(
                broll_kind="shows the problem",
                thin_film=True,
                needs=[{"what": "the cloudy film", "photos": [2]}],
            ),
            id="a thin film with a photo of it",
        ),
        pytest.param(
            a_plan_with(
                scenes=[
                    opening(line="Meet the stoneware MUG for your favourite warm drinks at home."),
                    PLAN["plan"]["scenes"][1],
                    closing(),
                ]
            ),
            id="the name said in other capitals",
        ),
    ],
)
def test_a_plan_that_keeps_the_rules_is_read(reply: dict[str, Any]) -> None:
    # The plans the broken ones below are made from are themselves fine, so each breaks
    # only the rule it is about.
    assert DECISION.model_validate(reply).plan is not None


@pytest.mark.parametrize(
    ("reply", "broken_rule"),
    [
        pytest.param(a_plan_with(scenes=[]), "List should have at least 1 item", id="no scenes"),
        pytest.param(
            a_plan_with(
                scenes=[
                    opening(overlay="Hand-thrown stoneware mug, only $24.00"),
                    PLAN["plan"]["scenes"][1],
                    closing(),
                ]
            ),
            "An overlay is a few words: 30 characters at most.",
            id="an overlay too long for its band",
        ),
        pytest.param(
            a_plan_with(scenes=[opening(line="  "), PLAN["plan"]["scenes"][1], closing()]),
            "A scene's line can't be empty.",
            id="a scene with nothing to say",
        ),
        pytest.param(
            a_plan_with(
                scenes=[
                    broll(
                        {
                            "line": "Hot tea pours into the mug for cosy mornings at home.",
                            "shows": "hot tea poured into the mug",
                        }
                    ),
                    opening(),
                    closing(),
                ]
            ),
            "The first scene is the person talking to camera",
            id="opening on a scene that shows the product",
        ),
        pytest.param(
            a_plan_with(
                scenes=[
                    opening(line="Meet my favourite mug, perfect for your hot drinks at home."),
                    broll(
                        {
                            "line": "Watch hot tea pouring into this mug for cosy home mornings.",
                            "shows": "tea poured into the mug",
                        }
                    ),
                    closing(),
                ]
            ),
            'No line says "Stoneware Mug": at least one must.',
            id="the name said in no line",
        ),
        pytest.param(
            broll_scene(kind=None),
            "Input should be 'talking' or 'broll'",
            id="no explicit scene kind",
        ),
        pytest.param(
            broll_scene(kind_why=""),
            "A B-roll scene needs why it cannot be shown at arm's length.",
            id="B-roll with no reason for its kind",
        ),
        pytest.param(
            talking_scene(shows="  "),
            "A talking scene needs an action with the product",
            id="a plain talking head",
        ),
        pytest.param(
            a_plan_with_ways("broll", "talking", "broll", "broll"),
            "With no target length, plan 3 or 4 scenes: the hook, 1 or 2 body scenes and the call "
            "to action.",
            id="five scenes without an owner target",
        ),
        pytest.param(
            # A B-roll scene filming what a talking scene already shows repeats it.
            a_plan_with(
                scenes=[
                    opening(),
                    broll(
                        {
                            "line": "It holds 350 ml for your favourite hot drink at home.",
                            "shows": "The presenter shows the mug to the lens",
                        }
                    ),
                    closing(),
                ]
            ),
            "Scenes 1 and 2 have the same action: a B-roll scene's action is in no other scene, "
            "though talking scenes may share one, such as the first and the last scene both "
            "showing the product to the lens.",
            id="a B-roll scene repeating a talking scene's action",
        ),
        pytest.param(
            broll_scene(broll_kind=None),
            'A B-roll scene needs its kind: "does a job", "shows the problem" or "showcase".',
            id="a B-roll scene with no kind",
        ),
        pytest.param(
            broll_scene(person_shown=None),
            'A B-roll scene needs who is in it: "no face" or "has face".',
            id="a B-roll scene with no person label",
        ),
        pytest.param(
            broll_scene(owner_part={**OWNER_PART, "looks": " "}),
            "This can't be empty.",
            id="an owner part with no looks",
        ),
        pytest.param(
            broll_scene(owner_part={**OWNER_PART, "what": ""}),
            "This can't be empty.",
            id="an owner part that is nothing",
        ),
        pytest.param(
            broll_scene(owner_part={**OWNER_PART, "source": "a photo"}),
            "Input should be \"owner's words\" or 'invented'",
            id="an owner part from neither",
        ),
        pytest.param(
            broll_scene(owner_part=OWNER_PART, needs=[{"what": "The  Handle", "photos": [2]}]),
            'Scene 2 has a photo of "the handle": a part a photo shows is a need, never an '
            "owner part.",
            id="an owner part a photo shows",
        ),
        pytest.param(
            broll_scene(broll_kind="looks good"),
            "Input should be 'does a job', 'shows the problem' or 'showcase'",
            id="a kind that is none of them",
        ),
        pytest.param(
            broll_scene(broll_kind="shows the problem", result="A clean mug."),
            'A "shows the problem" scene has no result.',
            id="a problem shown with a result",
        ),
        pytest.param(
            # Test ads (08 Oct), N3: wiping a film nobody could see looked the same before and
            # after. Without a photo of it, no scene is about it.
            broll_scene(broll_kind="shows the problem", thin_film=True),
            "A scene about a thin film needs a photo that shows it among its needs. Without "
            "one, make no scene about it: the voice says it over another B-roll.",
            id="a thin film with no photo of it",
        ),
        pytest.param(
            broll_scene(
                broll_kind="does a job", usage="Wipe it.", result="No film.", thin_film=True
            ),
            "A scene about a thin film needs a photo that shows it among its needs.",
            id="a thin film wiped away with no photo of it",
        ),
        pytest.param(
            broll_scene(broll_kind="does a job", usage=" ", result="A full mug."),
            'A "does a job" scene needs its usage: how the product is used in it.',
            id="a job done with no usage",
        ),
        pytest.param(
            broll_scene(broll_kind="does a job", usage="Pour tea in.", result=None),
            'A "does a job" scene needs its result: what you can see at its end.',
            id="a job done with no result",
        ),
        pytest.param(
            broll_scene(result="A full mug."),
            'A "showcase" scene has no result.',
            id="a showcase with a result",
        ),
        pytest.param(
            broll_scene(needs=[{"what": "the handle", "photos": [13]}]),
            "There's no picture 13 in the library.",
            id="a need naming a picture after the last one",
        ),
        pytest.param(
            broll_scene(needs=[{"what": "the handle", "photos": []}]),
            "List should have at least 1 item",
            id="a need naming no photo",
        ),
        pytest.param(
            broll_scene(needs=[{"what": " ", "photos": [1]}]),
            "This can't be empty.",
            id="a need that says nothing",
        ),
        pytest.param(
            broll_scene(
                scene_pictures=[{"number": 4, "for": "in-use"}, {"number": 13, "for": "result"}]
            ),
            "Scene 2 names picture 13: the library has no such picture.",
            id="a scene picture after the library's last",
        ),
        pytest.param(
            broll_scene(piece={"video": 1, "moment": 3}),
            "Scene 2 names moment 3 of video 1: that video has moments 1 to 2.",
            id="a moment after the video's last",
        ),
        pytest.param(
            broll_scene(piece={"video": 2, "moment": 1}),
            "Scene 2 names a moment of video 2: that video has no moments.",
            id="a moment of a video with none",
        ),
        pytest.param(
            a_plan_with(
                scenes=[
                    opening(),
                    broll(
                        {
                            "line": "It holds 350 ml for your favourite hot drink at home.",
                            "shows": "Tea poured into the mug",
                        }
                    ),
                    broll(
                        {
                            "line": "Keep your favourite hot drink warm in this handmade "
                            "stoneware mug.",
                            "shows": "  tea POURED into a mug. ",
                        }
                    ),
                ]
            ),
            "Scenes 2 and 3 have the same action: a B-roll scene's action is in no other scene, "
            "though talking scenes may share one, such as the first and the last scene both "
            "showing the product to the lens.",
            id="two broll scenes filming the same action",
        ),
        pytest.param(a_plan_with(product_name=" "), "This can't be empty.", id="no name"),
        pytest.param(a_plan_without("product_behaviour"), "Field required", id="no behaviour note"),
        pytest.param(
            a_plan_with(product_behaviour=" "), "This can't be empty.", id="an empty behaviour note"
        ),
        pytest.param(
            a_plan_with(product_behaviour="It holds a hot drink.\n\nIt is lifted by its handle."),
            "How the product behaves is one short paragraph.",
            id="a behaviour note of two paragraphs",
        ),
        pytest.param(a_plan_with(product_colour=" "), "This can't be empty.", id="no colour"),
        pytest.param(
            a_plan_with(person_looks=" "), "This can't be empty.", id="no look for the person"
        ),
        pytest.param(
            a_plan_with(person_voice=""), "This can't be empty.", id="no voice for the person"
        ),
        # The portrait and H3 voice must describe the same presenter.
        pytest.param(
            a_plan_without("person_gender"), "Field required", id="no gender for the person"
        ),
        pytest.param(
            a_plan_with(person_gender="adult"),
            "Input should be 'man' or 'woman'",
            id="a gender that is neither",
        ),
        # The starting picture's pose depends on how big the product is: a chair can't be
        # held at chest height, and studs held there are a few pixels.
        pytest.param(
            a_plan_without("product_size"), "Field required", id="no size for the product"
        ),
        pytest.param(
            a_plan_with_ways("talking", "second state"),
            "Scene 3 shows a second state, so scene 2 must be B-roll showing the first.",
            id="a second state after a talking scene",
        ),
        pytest.param(
            a_plan_with_ways("broll", "second state", "second state"),
            "Scene 4 shows a third state: one claim has two B-roll scenes at most.",
            id="a third state",
        ),
        pytest.param(
            a_plan_with(scenes=[opening(second_state=True), PLAN["plan"]["scenes"][1], closing()]),
            "Scene 1 is said to camera: only a B-roll scene shows a second state.",
            id="the opening scene given as a second state",
        ),
        pytest.param(
            a_plan_with_ways("broll", "talking second state"),
            "Scene 3 is said to camera: only a B-roll scene shows a second state.",
            id="a talking scene given as a second state",
        ),
        pytest.param(
            a_plan_with(product_size="medium"),
            "Input should be 'tiny', 'handheld' or 'large'",
            id="a size that isn't one of the three",
        ),
        pytest.param(
            a_plan_with(colour_photos=[1, 13]),
            "There's no picture 13 in the library.",
            id="a picture after the last one",
        ),
        pytest.param(
            a_plan_with(colour_photos=[0]),
            "There's no picture 0 in the library.",
            id="a picture before the first one",
        ),
        pytest.param(
            a_plan_with(product_photos=[1, 13]),
            "There's no picture 13 in the library.",
            id="a product photo the library doesn't hold",
        ),
        pytest.param(
            a_plan_with(real_size=" "),
            "No real size given. Give the product's real size",
            id="no real size",
        ),
        pytest.param(
            a_plan_with(colour_photos=["1"]),
            "Input should be a valid integer",
            id="a photo number given as text",
        ),
        pytest.param(
            {**PLAN, "plan": None},
            'A "plan" decision needs a plan and no question.',
            id="a plan decision with no plan",
        ),
        pytest.param(
            {**PLAN, "decision": "ask", "question": "Which size?"},
            'An "ask" decision needs a question and no plan.',
            id="a question and a plan",
        ),
    ],
)
def test_a_plan_that_breaks_a_rule_cant_be_read(reply: dict[str, Any], broken_rule: str) -> None:
    with pytest.raises(ValidationError) as refused:
        DECISION.model_validate(reply)

    assert broken_rule in str(refused.value)


# --- More B-roll, and talking scenes that differ (ticket #403) ------------------------------


def lens_opening(**changes: Any) -> dict[str, Any]:
    """The hook: a selfie, the mug out of its box, shown to the lens."""
    return {
        **opening(),
        "talking_style": "selfie",
        "product_state": "out of the box",
        "shows_to_lens": True,
        **changes,
    }


def sipping_close(**changes: Any) -> dict[str, Any]:
    """The call to action: propped, the mug in use."""
    return {
        **closing(),
        "shows": "The presenter sips tea from the mug",
        "talking_style": "propped",
        "product_state": "in use",
        **changes,
    }


def pouring(**changes: Any) -> dict[str, Any]:
    return broll(
        {
            "line": "The mug holds 350 ml of your favourite hot drink.",
            "shows": "Hot tea pours into the mug",
            **changes,
        }
    )


def drinking_middle(**changes: Any) -> dict[str, Any]:
    """A middle scene she talks in while drinking from the mug."""
    return {
        "kind": "talking",
        "line": "It keeps your tea hot while you sip it slowly at home.",
        "shows": "The presenter drinks from the mug as she talks",
        "talking_style": "talking while doing it",
        "product_state": "in use",
        **changes,
    }


def as_planned(*scenes: dict[str, Any]) -> dict[str, Any]:
    """PLAN with exactly these scenes: no style or state is filled in."""
    return conftest.a_plan_with(scenes=list(scenes))


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(as_planned(lens_opening(), pouring(), sipping_close()), id="the mug plan"),
        pytest.param(
            as_planned(
                lens_opening(),
                drinking_middle(),
                pouring(),
                sipping_close(product_state="out of the box"),
            ),
            id="a middle scene talking while doing it",
        ),
        pytest.param(
            as_planned(
                lens_opening(),
                pouring(),
                sipping_close(shows="The presenter shows the mug's handle to the lens"),
            ),
            id="the first and the last scene showing the product to the lens",
        ),
        pytest.param(
            as_planned(
                lens_opening(product_state="in its box", packaging_photo=2),
                pouring(),
                sipping_close(),
            ),
            id="a box state with its packaging photo",
        ),
        pytest.param(
            as_planned(
                lens_opening(),
                pouring(talking_style="selfie", product_state="out of the box"),
                sipping_close(),
            ),
            id="a B-roll scene's talking fields are not read",
        ),
    ],
)
def test_a_plan_with_broll_and_talking_scenes_that_differ_is_read(reply: dict[str, Any]) -> None:
    assert DECISION.model_validate(reply).plan is not None


TOLD_RULES_BROKEN = [
    pytest.param(
        as_planned(
            lens_opening(),
            drinking_middle(),
            sipping_close(product_state="beside its box", packaging_photo=2),
        ),
        AT_LEAST_ONE_BROLL,
        "The plan has no B-roll scene.",
        id="no B-roll scene",
    ),
    pytest.param(
        as_planned(
            lens_opening(),
            drinking_middle(talking_style="propped"),
            pouring(),
            sipping_close(product_state="out of the box"),
        ),
        MIDDLE_IS_BROLL,
        "Scene 2 is a middle scene talking in another style.",
        id="a middle scene talking propped",
    ),
    pytest.param(
        as_planned(
            lens_opening(),
            drinking_middle(shows_to_lens=True),
            pouring(),
            sipping_close(product_state="out of the box"),
        ),
        LENS_FIRST_OR_LAST,
        "Scene 2 only shows the product to the lens.",
        id="a middle scene only showing the product to the lens",
    ),
    pytest.param(
        as_planned(lens_opening(talking_style=None), pouring(), sipping_close()),
        STYLE_AND_STATE,
        "Scene 1 is a talking scene without a talking style or a product state",
        id="a talking scene with no style",
    ),
    pytest.param(
        as_planned(lens_opening(), pouring(), sipping_close(product_state=None)),
        STYLE_AND_STATE,
        "Scene 3 is a talking scene without a talking style or a product state",
        id="a talking scene with no product state",
    ),
    pytest.param(
        as_planned(lens_opening(product_state="in its box"), pouring(), sipping_close()),
        BOX_NEEDS_PACKAGING,
        'Scene 1 is "in its box" with no packaging photo.',
        id="in its box with no packaging photo",
    ),
    pytest.param(
        as_planned(lens_opening(), pouring(), sipping_close(product_state="beside its box")),
        BOX_NEEDS_PACKAGING,
        'Scene 3 is "beside its box" with no packaging photo.',
        id="beside its box with no packaging photo",
    ),
]


@pytest.mark.parametrize(("reply", "rule", "said"), TOLD_RULES_BROKEN)
def test_a_plan_breaking_a_told_broll_or_talking_rule_is_kept_and_says_which(
    reply: dict[str, Any], rule: str, said: str
) -> None:
    # None of these refused a plan in round 12: the planner is only told them, and a plan
    # breaking one goes ahead (Julian, Nov 9 2026, rules review).
    plan = DECISION.model_validate(reply).plan
    assert plan is not None

    not_kept = " ".join((told_rules_broken(plan.plan_scenes()) or "").split())
    assert said in not_kept
    assert rule in not_kept


def test_a_packaging_photo_the_library_does_not_hold_is_sent_back() -> None:
    reply = as_planned(
        lens_opening(product_state="in its box", packaging_photo=13),
        pouring(),
        sipping_close(),
    )
    with pytest.raises(ValidationError) as refused:
        DECISION.model_validate(reply)

    sent_back = " ".join(str(refused.value).split())
    assert "Scene 1 names picture 13 as its packaging photo" in sent_back
    assert "the library has no such picture" in sent_back


def test_the_planner_is_told_each_broll_and_talking_rule_in_the_words_a_notice_says() -> None:
    for rule in RULES:
        assert rule in PLAN_INSTRUCTIONS


def test_a_one_scene_ad_needs_no_broll_scene() -> None:
    # Its one scene is its first, which is always talking (spec #401, a target length).
    one = producer_decision_for(library=LIBRARY, moments=MOMENTS, target_seconds=8)
    assert one.model_validate(as_planned(lens_opening())).plan is not None


def test_a_broll_scene_stores_no_talking_style_product_state_or_packaging_photo() -> None:
    plan = DECISION.model_validate(
        as_planned(
            lens_opening(),
            pouring(talking_style="selfie", product_state="in its box", packaging_photo=2),
            sipping_close(),
        )
    ).plan
    assert plan is not None

    stored = plan.scenes[1].quality_fields()
    assert (stored["talking_style"], stored["product_state"], stored["packaging_photo"]) == (
        "",
        "",
        None,
    )


@pytest.mark.parametrize(
    "same",
    [
        pytest.param({"talking_style": "selfie"}, id="the same style either side of a B-roll"),
        pytest.param({"product_state": "out of the box"}, id="the same state either side"),
    ],
)
def test_talking_scenes_alike_in_style_or_state_are_told_but_never_sent_back(
    same: dict[str, str],
) -> None:
    # It compared labels, not pictures, and its refusals cost round 12's producer fixes a
    # price or a demo (Julian, Nov 8 2026): the planner is still told it.
    assert DIFFERENT_STYLE_AND_STATE in PLAN_INSTRUCTIONS
    plan = DECISION.model_validate(as_planned(lens_opening(), pouring(), sipping_close(**same)))

    assert plan.plan is not None


def test_a_plan_breaking_several_told_rules_names_every_one_once() -> None:
    reply = as_planned(
        lens_opening(talking_style=None, product_state="in its box"),
        drinking_middle(talking_style="propped"),
        sipping_close(),
    )
    plan = DECISION.model_validate(reply).plan
    assert plan is not None

    not_kept = " ".join((told_rules_broken(plan.plan_scenes()) or "").split())
    for rule in (
        AT_LEAST_ONE_BROLL,
        MIDDLE_IS_BROLL,
        STYLE_AND_STATE,
        BOX_NEEDS_PACKAGING,
    ):
        assert not_kept.count(rule) == 1
    assert DIFFERENT_STYLE_AND_STATE not in not_kept


# --- The emotional after in the last scene (Julian, Nov 8 2026) ------------------------------


def test_the_last_line_keeps_the_after_then_ask_in_every_step_that_writes_it() -> None:
    # Creatify's passing ads end on her talking: how it feels now, answering her hook, then a
    # friend's casual ask. Every step that can rewrite the last line keeps both beats.
    from agents.producer import INSTRUCTIONS as PRODUCER_INSTRUCTIONS
    from agents.scene_agent.instructions import INSTRUCTIONS as SCENE_AGENT_INSTRUCTIONS
    from jobs.planning import KEEPS_THE_LAST_SCENE, LAST_SCENE_FREE_HAND, LAST_SCENE_LINE
    from jobs.scene_rules import Place, rules_for

    for told in (LAST_SCENE_LINE, LAST_SCENE_FREE_HAND):
        assert told in PLAN_INSTRUCTIONS
    # The other steps get it with the last scene's rules (tests/test_scene_rules_sent.py).
    for told in (rules_for([Place(3, broll=False)], 3), PRODUCER_INSTRUCTIONS):
        assert KEEPS_THE_LAST_SCENE in told
    for told in (PRODUCER_INSTRUCTIONS, SCENE_AGENT_INSTRUCTIONS):
        assert LAST_SCENE_FREE_HAND in told


def test_a_plan_can_end_on_broll() -> None:
    scenes = [
        opening(),
        broll({"line": "Set it down.", "shows": "A hand sets the mug on the table"}),
        broll({"line": "It holds 350 ml.", "shows": "Hot tea pours into it"}),
    ]
    decision = DECISION.model_validate(a_plan_with(scenes=scenes))

    assert decision.plan is not None
    assert [scene.kind for scene in decision.plan.scenes] == ["talking", "broll", "broll"]


def test_a_rewrite_of_the_last_line_must_keep_saying_the_brand() -> None:
    from jobs.checks import (
        LineToCheck,
        rewritten_line_for,
        rewritten_scene_for,
        shortened_script_for,
    )

    dropped = {"line": "Yours for twelve bucks, honestly just get one."}
    kept = {"line": "Yours for twelve bucks from Kiln & Co, honestly just get one."}
    said = 'The last scene\'s line must say the brand, "Kiln & Co", in its casual ask.'

    with pytest.raises(ValidationError, match=re.escape(said)):
        rewritten_line_for("Kiln & Co").model_validate(dropped)
    assert rewritten_line_for("Kiln & Co").model_validate(kept).line == kept["line"]
    # Any other scene, or an ad with no brand, has none to keep.
    assert rewritten_line_for("").model_validate(dropped).line == dropped["line"]

    talking = rewritten_scene_for(
        3, shows_something=True, library=[1], kind="talking", brand="Kiln & Co"
    )
    with pytest.raises(ValidationError, match=re.escape(said)):
        talking.model_validate({**dropped, "shows": "She holds the mug up."})

    script = [
        LineToCheck(scene=number, line="A line.", shows=None, usage=None, result=None)
        for number in (1, 2)
    ]
    with pytest.raises(ValidationError, match=re.escape(said)):
        shortened_script_for(script, brand="Kiln & Co").model_validate(
            {"lines": [{"scene": 1, "line": "A hook."}, {"scene": 2, **dropped}]}
        )


def test_a_shortening_never_drops_the_last_scene() -> None:
    # Dropping it would end the ad on another scene, such as B-roll, with no after.
    from jobs.checks import LineToCheck, shortened_script_for

    script = [
        LineToCheck(scene=number, line="A line.", shows=None, usage=None, result=None)
        for number in (1, 2, 3)
    ]
    with pytest.raises(ValidationError, match="Keep the last scene, the call to action"):
        shortened_script_for(script).model_validate(
            {"lines": [{"scene": 1, "line": "A hook."}, {"scene": 2, "line": "From Kiln & Co."}]}
        )


def test_a_change_to_the_last_line_is_held_to_the_brand_only_if_it_said_it() -> None:
    from jobs.planning import brand_dropped

    said = "Yours for twelve bucks from Kiln & Co."
    plain = "Yours for twelve bucks."
    assert brand_dropped(said, plain, "Kiln & Co") is not None
    assert brand_dropped(said, said, "Kiln & Co") is None
    # A plan whose last line didn't say it before is held only to what it kept.
    assert brand_dropped(plain, plain, "Kiln & Co") is None
    assert brand_dropped(said, plain, "") is None
