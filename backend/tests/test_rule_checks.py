"""The code checks of the rule evals (decisions/rule-evals-plan.md), run on a saved run: one
folder per ad, one folder per scene with the inputs.txt the app's run wrote, and the shop
owner's answers. Each check is exact text, no AI, so it can't bend."""

import json
from pathlib import Path

import pytest

from evals.rule_checks import check_run

GOOD: dict[str, object] = {
    "LINE": "Scrub the ring once and the rust lifts right where the brush passes.",
    "SHOWS": "a toilet brush scrubs a rust stain away, with the cleaner bottle nearby",
    "BROLL_KIND": "does a job",
    "USAGE": "scrub with a toilet brush",
    "RESULT": "the rust stain is gone where the brush passed",
    "PICTURES SENT": [{"job": "the product, only how it looks", "image": 1, "photo": 1}],
    "START-PICTURE PROMPT": "An upright 9:16 photo in a real, ordinary bathroom. A brush rests "
    "on a rust ring.",
    "MOTION PROMPT": "The hand makes one firm scrubbing pass along the rust ring; the stain "
    "lifts only under the bristles.",
    "VIDEO PROMPT SENT": "A 5-second video at real-time speed. The camera stays still. The hand "
    "makes one firm scrubbing pass along the rust ring; the stain lifts only under the "
    "bristles.",
    "SETTINGS": "model=creatify/boreal-h3 seconds=5 starting_picture=start.png example_pictures=[]",
}


def inputs_txt(scene: dict[str, object]) -> str:
    """A scene's inputs.txt, laid out as the app's runs write it."""
    return "\n".join(
        [
            f"LINE: {scene['LINE']}",
            f"SHOWS: {scene['SHOWS']}",
            f"BROLL_KIND: {scene['BROLL_KIND']}",
            "PERSON_SHOWN: no face",
            f"USAGE: {scene['USAGE']}",
            f"RESULT: {scene.get('RESULT', '')}",
            "",
            "WAY: 1",
            f"PICTURES SENT: {json.dumps(scene['PICTURES SENT'])}",
            "",
            "START-PICTURE PROMPT (exact):",
            str(scene["START-PICTURE PROMPT"]),
            "",
            "START-PICTURE PROMPT REASON: a reason.",
            "",
            f"MOTION PROMPT (planned): {scene['MOTION PROMPT']}",
            "",
            "BOREAL CALL (succeeded, attempt 1, cost $0.396000, video_seconds 5.0):",
            "VIDEO PROMPT SENT (exact):",
            str(scene["VIDEO PROMPT SENT"]),
            f"SETTINGS: {scene['SETTINGS']}",
            "",
            "CLIP: version 1, seconds 5.207",
        ]
    )


def saved_run(
    tmp_path: Path,
    middle: list[dict[str, object]],
    *,
    ad: str = "8-toilet-cleaner",
    first_talking: bool = True,
    shop_answers: str = "",
) -> Path:
    """A saved run of one ad: a talking first scene, `middle` B-roll scenes, a talking last."""
    folder = tmp_path / ad
    first = folder / ("scene-1-talking" if first_talking else "scene-1")
    first.mkdir(parents=True)
    if not first_talking:
        (first / "inputs.txt").write_text(inputs_txt(GOOD))
    for number, scene in enumerate(middle, start=2):
        (folder / f"scene-{number}").mkdir()
        (folder / f"scene-{number}" / "inputs.txt").write_text(inputs_txt(scene))
    (folder / f"scene-{len(middle) + 2}-talking").mkdir()
    (tmp_path / "shop-answers.log").write_text(shop_answers)
    return tmp_path


def failed(run: Path) -> set[tuple[str, int, str]]:
    """(ad, scene, check) of every check that failed on the run."""
    return {
        (result.ad, result.scene, result.check) for result in check_run(run) if not result.passed
    }


def test_a_scene_that_keeps_every_rule_passes_every_check(tmp_path: Path) -> None:
    run = saved_run(tmp_path, [GOOD])

    results = check_run(run)

    assert results
    assert all(result.passed for result in results)


@pytest.mark.parametrize(
    ("change", "check"),
    [
        pytest.param(
            {
                "VIDEO PROMPT SENT": "A 5-second handheld phone video, casual. A hand scrubs.",
            },
            "A1 real-time speed",
            id="no real-time speed",
        ),
        pytest.param(
            {"MOTION PROMPT": "The hand slowly scrubs the ring for 3 seconds."},
            "A4 no timings",
            id="timings",
        ),
        pytest.param(
            # Round 2 #12 s5: the drop photo was sent with a job naming the drop.
            {
                "PICTURES SENT": [
                    {"job": "the product, only how it looks", "image": 1, "photo": 1},
                    {"job": "only how the edge-first drop looks", "image": 2, "photo": 2},
                ]
            },
            "A6 photo jobs are looks only",
            id="a photo sent for an action",
        ),
        pytest.param(
            {"SETTINGS": "model=creatify/boreal-h3 starting_picture= example_pictures=[a.png]"},
            "A7 drawn start picture, no shop photos",
            id="shop photos sent to the video model",
        ),
        pytest.param(
            # Round 1 #8 s2: the camera pushed toward the ring.
            {
                "MOTION PROMPT": "The person holding the phone pushes the camera forward toward "
                "the ring."
            },
            "A14 the camera never moves",
            id="camera moves",
        ),
        pytest.param(
            {"MOTION PROMPT": "After two seconds the hand scrubs the ring."},
            "A4 no timings",
            id="a timing in words",
        ),
        pytest.param(
            {"MOTION PROMPT": "The camera push-in toward the ring as the hand scrubs."},
            "A14 the camera never moves",
            id="camera push-in",
        ),
        pytest.param(
            {"MOTION PROMPT": "A slow pan across the bowl as the hand scrubs the ring."},
            "A14 the camera never moves",
            id="camera pan",
        ),
        pytest.param(
            {
                "LINE": "Dropped from 6 feet and still fine.",
                "START-PICTURE PROMPT": "A hand holds the phone 6 inches above a tile floor.",
            },
            "C3 a number in the scene is in its prompts",
            id="a number carried in the wrong unit",
        ),
        pytest.param(
            # Round 2 #12 s2: a hand just stands the phone up.
            {
                "SHOWS": "a hand sets the fitted case upright",
                "BROLL_KIND": "showcase",
            },
            "C1 the B-roll does more than handle the product",
            id="only handles the product",
        ),
        pytest.param(
            {"LINE": "Drop-tested 50 times from 6 feet and not a scratch."},
            "C3 a number in the scene is in its prompts",
            id="a number left out",
        ),
    ],
)
def test_a_scene_that_breaks_a_rule_fails_that_check(
    tmp_path: Path, change: dict[str, object], check: str
) -> None:
    run = saved_run(tmp_path, [{**GOOD, **change}])

    assert failed(run) == {("8-toilet-cleaner", 2, check)}


def test_a_scene_that_shows_the_problem_may_have_the_product_rest_beside_it(
    tmp_path: Path,
) -> None:
    problem = {
        **GOOD,
        "SHOWS": "the cleaner bottle rests in view beside a toilet bowl with a ring",
        "BROLL_KIND": "shows the problem",
    }

    assert failed(saved_run(tmp_path, [problem])) == set()


@pytest.mark.parametrize(
    ("shows", "kind"),
    [
        # Round 1 #5 bag, graded OK: placing it is the job, showing the bag holds a phone.
        ("a hand lowers a phone into the open bag beside a small wallet", "does a job"),
        # Round 1 #8 s4, graded fine: the bottle only stands by while the brush scrubs.
        (
            "a hand scrubs a rust ring with a toilet brush while the cleaner bottle stands by",
            "showcase",
        ),
    ],
)
def test_handling_the_product_that_is_not_the_scene_s_one_action_passes(
    tmp_path: Path, shows: str, kind: str
) -> None:
    assert failed(saved_run(tmp_path, [{**GOOD, "SHOWS": shows, "BROLL_KIND": kind}])) == set()


def test_a_number_the_prompts_carry_in_words_passes(tmp_path: Path) -> None:
    drop = {
        **GOOD,
        "LINE": "Dropped from six feet and still fine.",
        "START-PICTURE PROMPT": "A hand holds the phone 6 feet above a tile floor.",
    }

    assert failed(saved_run(tmp_path, [drop])) == set()


def test_an_ad_that_opens_on_b_roll_fails(tmp_path: Path) -> None:
    run = saved_run(tmp_path, [GOOD], first_talking=False)

    assert ("8-toilet-cleaner", 1, "A16 the first scene is talking") in failed(run)


def test_after_the_shop_owner_has_no_before_and_after_photo_no_middle_scene_is_b_roll(
    tmp_path: Path,
) -> None:
    # Round 2 N3: the owner had no before-and-after photo, and the app filmed filler.
    answers = (
        "N3: Q 'Could you attach a before-and-after photo of the shower or tile, or should I "
        "proceed without one?' -> A \"I don't have a before-and-after photo. Go ahead without "
        'one."\n'
    )
    run = saved_run(tmp_path, [GOOD, GOOD], ad="N3-shower-tile-cleaner", shop_answers=answers)

    assert failed(run) == {
        ("N3-shower-tile-cleaner", 2, "C4 no B-roll filler after the owner has no photo"),
        ("N3-shower-tile-cleaner", 3, "C4 no B-roll filler after the owner has no photo"),
    }


def test_a_before_and_after_photo_the_owner_gave_keeps_the_b_roll(tmp_path: Path) -> None:
    answers = "N3: Q 'Could you attach a before-and-after photo of the tile?' -> A 'Here it is.'\n"
    run = saved_run(tmp_path, [GOOD], ad="N3-shower-tile-cleaner", shop_answers=answers)

    assert failed(run) == set()


def test_an_ad_folder_with_scene_files_but_no_scene_folders_is_skipped(tmp_path: Path) -> None:
    # runs/graded-broll-run/08-planner-a-vs-b keeps scene-2-*.txt files, not scene folders.
    run = saved_run(tmp_path, [GOOD])
    loose = tmp_path / "8-planner-a-vs-b"
    loose.mkdir()
    (loose / "scene-2-plan.txt").write_text("a plan")

    assert {result.ad for result in check_run(run)} == {"8-toilet-cleaner"}
