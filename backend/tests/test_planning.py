"""The planner's rules, tried on the plan itself: what its instructions tell it, what the
decision's schema says of each field, and which answers can't be read. No chat and no
database: the plan is read as the gateway reads it, with `producer_decision_for`, and the
chat's own tests in test_producer_plan.py prove the instructions and a refused plan reach
and leave it the same way."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from jobs.planning import PLAN_INSTRUCTIONS, producer_decision_for

from .conftest import FINISHING_STEP, PLAN, SHOWCASE, a_plan_with, broll

# The decision for a job with the mug's 2 photos, as the planner is asked for it.
PHOTO_COUNT = 2
DECISION = producer_decision_for(PHOTO_COUNT)
# What the planner is told of each field, as its schema is given to the model.
SCHEMA = DECISION.model_json_schema()["$defs"]


def broll_scene(**labels: Any) -> dict[str, Any]:
    """PLAN with a second scene showing the mug while its line is said, labelled as a
    showcase with no face but for `labels`."""
    scene = {"line": "Holds 350 ml.", "shows": "the mug on a shelf", **SHOWCASE, **labels}
    return a_plan_with(scenes=[{"line": "Meet the Stoneware Mug."}, scene])


def talking_scene(**labels: Any) -> dict[str, Any]:
    """PLAN with a second scene the person says to camera, given B-roll `labels`."""
    return a_plan_with(
        scenes=[{"line": "Meet the Stoneware Mug."}, {"line": "Holds 350 ml.", **labels}]
    )


def a_plan_without(field: str) -> dict[str, Any]:
    """PLAN with one of its plan's fields left out altogether."""
    return {**PLAN, "plan": {name: value for name, value in PLAN["plan"].items() if name != field}}


def a_plan_with_ways(*kinds: str) -> dict[str, Any]:
    """PLAN with a scene of each kind after its opening line: "talking" to camera,
    "broll" showing the mug, "second state" showing the second of two end states, or
    "talking second state", said to camera yet given as a second state."""
    scenes: list[dict[str, Any]] = [{"line": "Meet the Stoneware Mug."}]
    for kind in kinds:
        line = {"line": "Holds 350 ml of hot tea or cold brew."}
        if kind == "talking":
            scenes.append(line)
        elif kind == "talking second state":
            scenes.append({**line, "second_state": True})
        else:
            scenes.append(broll({**line, "shows": "the mug in a hand"}))
            scenes[-1]["second_state"] = kind == "second state"
    return a_plan_with(scenes=scenes)


# --- What the planner is told ----------------------------------------------------------------


def test_the_planner_is_told_how_to_plan_each_broll_scene() -> None:
    instructions = PLAN_INSTRUCTIONS
    # Test A in docs/plans/broll-boreal-h3.md: with this hint, 0 of 39 B-roll lines took
    # under 4 seconds to say. Any upper number makes the planner squeeze good lines, and "one
    # short sentence" disagrees.
    assert "A B-roll line has at least about 10 words." in instructions
    assert "short sentence" not in instructions
    # One action per B-roll scene: graded #8 toilet (4 steps in one clip) and #5
    # bag (strap on and off in one clip) failed; one step, and one scene per way of use, were
    # graded perfect. (The two-end-states sentence, the second state marked, and attaching or
    # adjusting as a movement of its own are pinned word for word in test_frozen_rules.py.)
    assert "A B-roll scene films one action" in instructions
    # One movement, no exceptions (Julian, 19:11): N5's clip jumped where Boreal skipped the
    # middle of three steps that flowed into each other.
    assert "even for steps that flow into each other" in instructions
    # Test ads (08 Oct): the toilet's "360-degree coverage under the rim" was only said in a
    # talking scene, never shown (point 3b).
    assert "name the other steps" not in instructions
    # Test ads (08 Oct), N5 Le Duo: the turn was split off the glide ("already clamped and
    # rotated"), so it filmed the page's straightening how-to under a line about curls.
    assert "or turning it" not in instructions
    assert (
        "Still pick only ONE action. Write that one action the way the page words it. If the "
        "page says something happens while doing it, keep that part."
    ) in instructions
    assert 'A part the page says happens while doing it stays, joined by "while".' in instructions
    # Test ads (08 Oct), #12 phone case: turned, dropped and rotated from start pictures of
    # one side, the clips drew the back on both sides. (The "never turns, flips or spins"
    # sentence and the camera-move ban are pinned in test_frozen_rules.py.)
    assert (
        "the side facing the camera when the scene starts faces it when the scene ends, even "
        "after a drop or fall"
    ) in instructions
    assert "give that side its own B-roll scene that starts on it" in instructions
    assert "with the product already set up that way" in instructions
    # Round 1 (08 Oct): N5 still wrote "twists and glides". One verb is checkable.
    assert "with one verb for what moves" in instructions
    # Two end states are two clips with a cut (graded #5 bag, PERFECT); #12's drop ended on
    # the phone face down, its unharmed screen never seen, and turning it over drew the back
    # on both sides (test ads, 08 Oct).
    assert "next B-roll scene starts on the other side, never turning it over" in instructions
    assert "turned over to show" not in instructions
    # The ad's colour, and the one photo a scene may never need: items 43 and 44 of
    # docs/broll-picture-logic.md.
    assert (
        "pick the colour with the most photos where the product is clearly seen; on a tie, "
        "the colour of Photo 1"
    ) in instructions
    assert "Never name a photo that shows the product in another colour" in instructions
    # Only the presenter's face: item 27. "Showcase when unsure" made filler B-roll of a hand
    # holding or setting down the product, and every one failed (round 2, #12 s2, #8 s4, N3).
    assert '"showcase" when unsure' not in instructions
    assert "The only person ever shown is the presenter" in instructions
    scene = SCHEMA["PlannedScene"]["properties"]
    assert set(scene) >= {"broll_kind", "person_shown", "usage", "result", "needs"}


def test_the_planner_is_told_the_shape_of_every_script() -> None:
    instructions = PLAN_INSTRUCTIONS
    # Creatify's own words, research/creatify-script-shape-originals.md in the project files:
    # "The first 3 seconds determine whether someone watches your ad.", "Match the structure
    # to your goal", "Pick one. Don't mix frameworks in the same video.", "One ad, one idea."
    # and "'Learn more' is not a CTA."
    assert "the hook, the body and the call to action" in instructions
    assert "not the product's name" in instructions
    assert "choose the one format that suits the product" in instructions
    for script_format, parts in [
        ("problem, agitate, solve", "the problem, agitate, solve"),
        ("feature cascade", "the hero feature, a supporting feature, proof"),
        ("before and after", "the before state, the transformation moment, the after state"),
        ("day in the life", "the routine, the key moment, the result"),
    ]:
        assert f'- "{script_format}": {parts}' in instructions
    assert "The ad is about one idea: the product's main benefit." in instructions
    assert "never make one up" in instructions
    assert "get it now" in instructions
    assert 'never "learn more"' in instructions
    assert "part" in SCHEMA["PlannedScene"]["properties"]
    assert "script_format" in SCHEMA["Plan"]["properties"]


def test_the_planner_is_told_every_middle_scene_is_broll_that_proves_its_line() -> None:
    # Test ads (08 Oct): 7 talking scenes in the middle of 5 ads. Julian 23:45 "Yes": scene 1
    # talking, every middle scene a B-roll. Round 2 (09 Oct): middles with nothing to prove
    # were filled with the product held or set down, and all failed. Julian 04:27: "it must
    # ... prove something about the product ... If we don't have enough data to do that, ask
    # from the shop owner. And if the shop owner cannot provide it, fall back to the talking
    # scene." (The first-scene sentence and "a claim ... a camera could see is a B-roll scene
    # that shows it" are pinned in test_frozen_rules.py.)
    instructions = PLAN_INSTRUCTIONS
    assert "Most scenes are the person talking to camera" not in instructions
    assert (
        "The last scene is the person talking to camera, or a B-roll scene that proves its "
        "line. Every scene between them is a B-roll scene that proves its line"
    ) in instructions
    assert "never the person talking" not in instructions
    assert "a B-roll scene of the product at its best" not in instructions
    assert (
        "When no B-roll kind could prove a middle scene's line, ask the shop owner for what "
        "would show it, as below; if they can't give it, that scene is the person talking to "
        "camera, never a B-roll of the product only held, placed, set down, stood up or "
        "pointed at."
    ) in instructions
    assert (
        'A line that can\'t be filmed, such as "no bleach", is still kept: the voice says it '
        "while the scene proves another claim the page states, never acting the claim out, "
        "or, when no scene could, the person says it to camera."
    ) in instructions
    # No share of B-roll per kind of product: our own research, not Creatify's.
    assert "%" not in instructions
    assert "never skin before and after" in instructions


def test_the_planner_asks_the_shop_owner_when_no_broll_could_prove_a_line() -> None:
    # Round 2 fixes 1 and 2 (Julian 04:27, 21:40 "we want [to merge] fix one and fix two"):
    # prove it, else ask the shop owner, else the person says it to camera. N3's thin film,
    # refused, became a filler B-roll ("over another B-roll"): now it is said to camera.
    instructions = PLAN_INSTRUCTIONS
    assert "Ask because of a scene that shows the product in only five cases" in instructions
    assert (
        "- A middle scene's line is one a camera could film, but no B-roll kind could prove "
        "it: nothing the page, the photos or the shop owner give shows it, and only a hand "
        "holding, placing, setting down, standing up or pointing at the product would be left. "
        'A line a camera can\'t film, such as "no bleach", is never asked about: it follows '
        "the rule for a line that can't be filmed."
    ) in instructions
    # Review P3 7: "at its best" means nothing since the showcase change.
    assert "Ask even if you could plan it only showing the product:" in instructions
    assert "only at its best" not in instructions
    assert "for a line nothing proves, of what would prove it" in instructions
    assert (
        "For a line nothing proves: if they go ahead without one, the person says that line "
        "to camera."
    ) in instructions
    assert "the voice says the claim over another B-roll." not in instructions
    assert (
        "the voice says the claim over another B-roll that proves its own line, or, when none "
        "does, the person says it to camera."
    ) in instructions


def test_the_planner_is_told_the_one_action_lasts_the_whole_clip() -> None:
    # Round 5 cause 2 (runs/round-5/root-cause): every one-moment action stretched over a
    # 5-7 s clip broke, 37 clips and no exception. Julian 2026-10-19 00:44: "one rule is good
    # and general (the action must fill the clip)" ... "I approve this one."
    assert (
        "That one action lasts the whole clip, which is as long as its line takes to say and "
        "never under 5 seconds: by its own course, such as a steady pour or a walk, or by what "
        "it naturally sets off in the same scene, such as a cushion tossed onto a sofa that "
        "bounces and settles. An action that is over in a moment never gets a scene that ends "
        "as the moment ends, and never gets a second action added to fill the time. When no "
        "single action can last the whole clip, the person says that line to camera."
    ) in PLAN_INSTRUCTIONS


def test_the_planner_films_a_use_only_when_it_is_shown_how_or_asks() -> None:
    # Round 5 cause 1 (runs/round-5/root-cause/1-planner-usage.md): a use the page only
    # named was taken as licence to film it any way, and the video model invented it.
    # Julian 2026-10-19 00:44: "film only what the page shows, else ask the owner" ... "I
    # approve this one." Round 2 (03:36, "i approve. but don't tell the owner to send the
    # short video"): steps or general words let the washer and the flossing through, so only
    # text saying how it is held and moved counts, and the owner is asked for a photo or one
    # sentence. 03:39: "we can infer how it can be machine washed ... You don't need to ask
    # this": an ordinary use anyone can picture from the words is never asked about.
    instructions = PLAN_INSTRUCTIONS
    assert (
        "A B-roll scene films the product being used only in a way it is shown how: a photo "
        "shows the product used that way, or the page's text or the shop owner's own words "
        "say how it is held and moved: which hand holds it, what moves and what it touches. "
        "Naming a use, listing its steps or describing it in general words doesn't show how. "
        "An ordinary use anyone can picture from the words needs neither, such as pouring "
        "from a bottle or zipping up a jacket: film its plain visible part. For a use nobody "
        "could picture from the words, ask the shop owner, as below."
    ) in instructions
    assert "Ask because of a scene that shows the product in only five cases" in instructions
    assert (
        "- The scene films the product being used in a way it isn't shown how, and nobody "
        "could picture it from the words: no photo shows it used that way and neither the page "
        "nor the shop owner says how it is held and moved."
    ) in instructions
    assert (
        "for a use it isn't shown how, of the product being used that way, or one sentence on "
        "how it is used"
    ) in instructions
    assert (
        "For a use it isn't shown how: if they go ahead without one, the person says that "
        "line to camera, or the voice says it over another B-roll scene that proves its own "
        "line."
    ) in instructions


def test_the_planner_films_only_what_a_still_camera_could_watch_from_outside() -> None:
    # Round 1 re-plan (2026-10-19): water sprayed inside a dishwasher and shoes in a running
    # washer, which the video model has to invent. Julian 03:36: "i approve".
    assert (
        "A B-roll scene films only what a still camera could watch happening from outside, the "
        "whole time. Something that happens inside a closed or running thing, or out of sight, "
        "is never a scene: the voice says it over another B-roll scene that proves its own "
        "line, or the person says it to camera."
    ) in PLAN_INSTRUCTIONS


def test_the_planner_is_told_a_showcase_only_when_using_it_is_the_proof() -> None:
    # Round 2: every "showcase" (a hand holding, setting down or standing up the product)
    # failed: #12 s2, #8 s4, N3 s2-5. Julian 02:15: "The B-roll must do something. It must
    # sell the product." The bag carried as a clutch and worn crossbody was graded PERFECT
    # (#5, decisions/2026-10-07-broll-prompts.md), so wearing it the way the line claims stays.
    instructions = PLAN_INSTRUCTIONS
    assert '"showcase" for everything else' not in instructions
    assert (
        '"showcase" only when using or wearing the product the way the line claims is itself '
        "the proof, such as a bag carried as a clutch or worn crossbody hands-free. A hand only "
        "holding, placing, setting down, standing up or pointing at the product is never a "
        "B-roll scene."
    ) in instructions
    assert "When no kind fits, the scene is not B-roll." in instructions
    # Review P3 5: a problem scene is a hand or the product beside the problem; the problem
    # in plain view is what proves its line, so the ban above doesn't cover it.
    assert (
        'In a "shows the problem" scene, the problem in plain view is what proves its line.'
    ) in instructions
    kind = SCHEMA["PlannedScene"]["properties"]["broll_kind"]
    assert '"showcase" when unsure' not in kind["description"]
    assert "such as a bag carried as a clutch" in kind["description"]


def test_the_planner_is_told_a_result_is_filmed_happening() -> None:
    # Round 2 #8 s4: the bottle beside a clean toilet. Julian: "there is no evidence that the
    # toilet is so clean right now ... we have to show."
    assert (
        "A result is filmed happening, from before to after in the same clip. A clean or "
        "finished thing with the product beside it, with no change filmed, never shows a "
        "result."
    ) in PLAN_INSTRUCTIONS


def test_the_planner_is_told_a_finishing_step_gets_no_broll_scene() -> None:
    # Round 2 fixes free check (10 Oct): #8 got a B-roll scene of the flush alone, with
    # stains still in the bowl and a different toilet from the scrub. Julian 02:59: add it.
    assert FINISHING_STEP in PLAN_INSTRUCTIONS


def test_the_planner_is_told_a_scene_needs_only_how_a_thing_looks() -> None:
    # Round 2 #12 s5: its need was "the edge-first drop", photo 2, the shop's drop photo; the
    # start picture copied its low hand, shoe and camera height. Julian 03:58: the picture
    # model copies the photo whatever its job says.
    assert (
        "- Its needs: only how a thing looks that the main photo can't show, such as what a "
        "gel looks like out of the tube. Never an action, a pose or a movement, such as a "
        "drop or a hand holding it: the action is said in words only."
    ) in PLAN_INSTRUCTIONS
    needs = SCHEMA["PlannedScene"]["properties"]["needs"]
    assert "only how a thing looks" in needs["description"]


def test_the_planner_offers_three_answers_for_something_of_the_product_no_photo_shows() -> None:
    # Owner part loop (spec handoffs/owner-part-approval, Julian Oct 19, Q6 "the ask offers
    # all three"): most shop owners have no photo of such a part, and "go ahead without it"
    # lost every scene that needed it. They can now describe it or have it drawn, and approve
    # the picture before its video is made.
    instructions = PLAN_INSTRUCTIONS
    assert (
        "For something of the product no photo shows, offer three answers: attach a photo of "
        "it; describe how it looks in one sentence; or have you draw it from your own idea of "
        "it. Say that they will see the scene's picture and say yes to it before its video is "
        "made."
    ) in instructions
    # The other three cases keep today's two answers.
    assert "For the other cases, offer two answers: attach a photo of it" in instructions
    assert (
        "if they attach a photo, plan with it among the scene's needs; if they describe it, "
        "give the scene an owner part from the shop owner's words, its looks their sentence "
        "as they said it; if they say to draw it, give the scene an owner part you invent, "
        "its looks one sentence of how such a thing ordinarily looks: the only thing you may "
        "make up."
    ) in instructions
    # A part no photo shows is never drawn, except the one the shop owner will approve.
    assert "parts of the product no photo shows, other than a scene's owner part" in instructions
    owner_part = SCHEMA["PlannedScene"]["properties"]["owner_part"]
    assert "described it or asked you to draw it" in owner_part["description"]
    part = SCHEMA["OwnerPart"]["properties"]
    assert part["source"]["enum"] == ["owner's words", "invented"]
    assert "their sentence as they said it" in part["looks"]["description"]


def test_the_planner_is_told_to_ask_for_a_before_and_after_nobody_could_picture() -> None:
    instructions = PLAN_INSTRUCTIONS
    # N3's shower cleaner was planned with a result nobody could picture and
    # no photo of it, so the video guessed; a dirty toilet scrubbed clean needed no photo.
    assert "in only five cases" in instructions
    # N3's soap scum was judged common knowledge, so it never asked: what decides is whether
    # a camera can see it, not whether people know the word.
    assert "a thin film, haze, cloudiness or water spots" in instructions
    assert "even when everyone knows the word" in instructions
    assert "a coloured mark anyone sees at a glance" in instructions
    assert "of it before and after" in instructions
    # Only a photo shows it: a page that says "removes the film" still leaves its look to a
    # guess, which is how N3's result was invented.
    assert "The page naming the result isn't enough" in instructions
    # Test ads (08 Oct), N3: told no, the planner offered wiping clean tile "without an
    # invented before view", and before looked the same as after. Julian: "the shop owner
    # must provide it."
    assert "without one, the ad shows no before and after of it" in instructions
    # Round 2 fix 2: "over another B-roll" made N3's filler; with none that proves its own
    # line, the person says the claim to camera.
    assert (
        "For a result: if they go ahead without one, no scene shows that film, before or "
        "after, and the voice says the claim over another B-roll that proves its own line, or, "
        "when none does, the person says it to camera."
    ) in instructions
    assert "Never offer another way to show it." in instructions
    assert 'For a missing "how to use": if they go ahead without one, ask again' in instructions


def test_the_planner_is_told_a_line_naming_the_problem_shows_the_problem() -> None:
    # Test ads (08 Oct), N3: the "builds up" line was talking; Julian: "showing like how the
    # showers can get dirty over time. As a B-roll scene." Creatify: "Before state (3-5s):
    # Show the problem visually".
    assert (
        '"shows the problem" when the line names the problem the product fixes, as the page '
        "names it: the problem as it really is before the product is used, with the product "
        "in view. Only a problem anyone sees at a glance, or one a photo shows. Never the "
        "problem getting worse or building up over time: show it as it is, and the voice says "
        "the rest"
    ) in PLAN_INSTRUCTIONS
    scene = SCHEMA["PlannedScene"]["properties"]
    assert "shows the problem" in json.dumps(scene["broll_kind"])
    assert scene["thin_film"]["description"] == (
        "True when the problem this scene shows, or the result it ends on, is a thin film, "
        "haze, cloudiness or water spots on a surface."
    )


def test_the_planner_is_told_to_copy_the_products_name_word_for_word_from_a_line() -> None:
    # A page's title often has more in it than anyone says, such as "Anker 313 Power Bank
    # (PowerCore 10K)" or "hydro-stars® + big yellow". Given as the name, it's in no line, so
    # the plan is refused and paid for again: 9 of the first 122 plans were.
    assert (
        "Give the product's name exactly as the person says it in one of those lines, copied "
        "word for word from it: not the page's full title, and nothing the person doesn't say, "
        "such as a part in brackets or a symbol like ® or ™."
    ) in PLAN_INSTRUCTIONS
    assert SCHEMA["Plan"]["properties"]["product_name"]["description"] == (
        "The product's name copied word for word from a line, exactly as the person says it "
        "there: not the page's full title."
    )


# --- What is read from the plan ---------------------------------------------------------------


def test_a_first_scene_given_a_blank_shows_is_the_person_talking() -> None:
    scenes = [
        {"line": "Meet the Stoneware Mug from Kiln & Co.", "shows": " "},
        broll({"line": "Hand-thrown, holds 350 ml.", "shows": "hot tea poured into the mug"}),
    ]

    decision = DECISION.model_validate(a_plan_with(scenes=scenes))

    assert decision.plan is not None
    assert [scene.shows for scene in decision.plan.scenes] == [None, "hot tea poured into the mug"]


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
        pytest.param(a_plan_with_ways("broll", "second state"), id="two end states"),
        # The main photo, three needed photos and the presenter's portrait.
        pytest.param(
            broll_scene(
                person_shown="has face",
                needs=[{"what": f"part {n}", "photos": [1]} for n in range(1, 4)],
            ),
            id="a scene sending five pictures",
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
                scenes=[{"line": "Meet the stoneware MUG."}, {"line": "Yours for $24.00."}]
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
            a_plan_with(scenes=[{"line": "  "}]),
            "A scene's line can't be empty.",
            id="a scene with nothing to say",
        ),
        pytest.param(
            a_plan_with(
                scenes=[
                    {
                        "line": "Yours for $24.00.",
                        "overlay": "Hand-thrown stoneware mug, only $24.00",
                    }
                ]
            ),
            "An overlay is a few words: 30 characters at most.",
            id="an overlay too long for its band",
        ),
        pytest.param(
            a_plan_with(
                scenes=[
                    broll({"line": "Hot tea, poured.", "shows": "hot tea poured into the mug"}),
                    {"line": "Meet the Stoneware Mug."},
                ]
            ),
            "The first scene is the person talking to camera",
            id="opening on a scene that shows the product",
        ),
        pytest.param(
            a_plan_with(
                scenes=[
                    {"line": "Meet my favourite mug."},
                    broll({"line": "The mug, poured.", "shows": "tea poured into the mug"}),
                ]
            ),
            'No line says "Stoneware Mug": at least one must.',
            id="the name said in no line",
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
            talking_scene(owner_part=OWNER_PART),
            "A scene where the person talks to camera has no owner part.",
            id="an owner part on a talking scene",
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
            talking_scene(thin_film=True),
            "A scene where the person talks to camera has no B-roll kind",
            id="a talking scene about a thin film",
        ),
        pytest.param(
            talking_scene(broll_kind="showcase"),
            "A scene where the person talks to camera has no B-roll kind, person, usage, "
            "result or needs.",
            id="a talking scene with a kind",
        ),
        pytest.param(
            talking_scene(person_shown="no face"),
            "A scene where the person talks to camera has no B-roll kind",
            id="a talking scene with a person label",
        ),
        pytest.param(
            talking_scene(needs=[{"what": "the handle", "photos": [1]}]),
            "A scene where the person talks to camera has no B-roll kind",
            id="a talking scene with needs",
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
            broll_scene(needs=[{"what": "the handle", "photos": [3]}]),
            "There's no photo 3: the job has 2.",
            id="a need naming a photo after the last one",
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
                person_shown="has face",
                needs=[{"what": f"part {n}", "photos": [1]} for n in range(1, 5)],
            ),
            "Scene 2 would send 6 pictures (the main photo, one for each of its 4 needs and "
            "the presenter's portrait): 5 at most.",
            id="a scene sending six pictures",
        ),
        pytest.param(a_plan_with(product_name=" "), "This can't be empty.", id="no name"),
        pytest.param(a_plan_with(product_colour=" "), "This can't be empty.", id="no colour"),
        pytest.param(
            a_plan_with(person_looks=" "), "This can't be empty.", id="no look for the person"
        ),
        pytest.param(
            a_plan_with(person_voice=""), "This can't be empty.", id="no voice for the person"
        ),
        # The voice and the portrait are each designed from words alone, and each picks a
        # gender at random when the words don't give one. So the plan must say it, as a
        # choice code enforces rather than a word the planner may leave out.
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
            a_plan_with(scenes=[{"line": "Meet the Stoneware Mug.", "second_state": True}]),
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
            a_plan_with(colour_photos=[]),
            "List should have at least 1 item",
            id="no photo showing the colour",
        ),
        pytest.param(
            a_plan_with(colour_photos=[1, 3]),
            "There's no photo 3: the job has 2.",
            id="a photo after the last one",
        ),
        pytest.param(
            a_plan_with(colour_photos=[0]),
            "There's no photo 0: the job has 2.",
            id="a photo before the first one",
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
