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
    "SETTINGS": "model=creatify/boreal-h3 seconds=5 starting_picture=make_starting_picture_Ab1.png "
    "example_pictures=[]",
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
            # Reference mode (Julian 2026-10-16): a raw photo after the drawn Image 1.
            {
                "SETTINGS": "model=creatify/boreal-h3 seconds=5 starting_picture= "
                "example_pictures=[make_starting_picture_N21pGSn.png, "
                "sessions/4/photos/3f2a.jpg]"
            },
            "A7 drawn start picture, no shop photos",
            id="a raw photo after the drawn starting picture",
        ),
        pytest.param(
            # Image 1 must be the drawn starting picture: the clip starts like it.
            {
                "SETTINGS": "model=creatify/boreal-h3 seconds=5 starting_picture= "
                "example_pictures=[make_reference_photo_picture_x1.png, "
                "make_starting_picture_N21pGSn.png]"
            },
            "A7 drawn start picture, no shop photos",
            id="image 1 not the drawn starting picture",
        ),
        pytest.param(
            {"SETTINGS": "model=creatify/boreal-h3 starting_picture= example_pictures=[]"},
            "A7 drawn start picture, no shop photos",
            id="no picture at all",
        ),
        pytest.param(
            # Both ways at once, as the video model refuses: a shop photo with the start one.
            {
                "SETTINGS": "model=creatify/boreal-h3 starting_picture=make_starting_picture_1.png "
                "example_pictures=[sessions/4/photos/3f2a.jpg]"
            },
            "A7 drawn start picture, no shop photos",
            id="a shop photo beside a starting picture",
        ),
        pytest.param(
            # Starting-picture mode with a raw photo as the first frame.
            {
                "SETTINGS": "model=creatify/boreal-h3 starting_picture=sessions/4/photos/3f2a.jpg "
                "example_pictures=[]"
            },
            "A7 drawn start picture, no shop photos",
            id="a raw photo as the starting picture",
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


@pytest.mark.parametrize(
    "sent_with",
    [
        pytest.param(
            "model=creatify/boreal-h3 seconds=5 starting_picture= "
            "example_pictures=[make_starting_picture_N21pGSn.png]",
            id="reference mode, the drawn starting picture as image 1",
        ),
        pytest.param(
            "model=creatify/boreal-h3 seconds=5 starting_picture= "
            "example_pictures=[make_starting_picture_N21pGSn.png, "
            "make_reference_photo_picture_x1.png]",
            id="reference mode, then a clean picture of the shop owner's thing",
        ),
        pytest.param(
            # As round 2 saved it, in starting-picture mode.
            "model=creatify/boreal-h3 (Boreal-H3) seconds=7 resolution=768p "
            "prompt_enhancement=auto starting_picture=make_starting_picture_3p9bZly.png "
            "example_pictures=[]",
            id="an old saved run in starting-picture mode",
        ),
    ],
)
def test_the_drawn_starting_picture_sent_first_either_way_passes(
    tmp_path: Path, sent_with: str
) -> None:
    run = saved_run(tmp_path, [{**GOOD, "SETTINGS": sent_with}])

    assert failed(run) == set()


@pytest.mark.parametrize(
    ("change", "shop_answers"),
    [
        pytest.param(
            {
                "SHOWS": "the cleaner bottle rests in view beside a toilet bowl with a ring",
                "BROLL_KIND": "shows the problem",
            },
            "",
            id="a scene that shows the problem has the product rest beside it",
        ),
        pytest.param(
            # Round 1 #5 bag, graded OK: placing it is the job, showing the bag holds a phone.
            {
                "SHOWS": "a hand lowers a phone into the open bag beside a small wallet",
                "BROLL_KIND": "does a job",
            },
            "",
            id="handling the product is the scene's one action",
        ),
        pytest.param(
            # Round 1 #8 s4, graded fine: the bottle only stands by while the brush scrubs.
            {
                "SHOWS": "a hand scrubs a rust ring with a toilet brush while the cleaner "
                "bottle stands by",
                "BROLL_KIND": "showcase",
            },
            "",
            id="the product stands by the scene's one action",
        ),
        pytest.param(
            {
                "LINE": "Dropped from six feet and still fine.",
                "START-PICTURE PROMPT": "A hand holds the phone 6 feet above a tile floor.",
            },
            "",
            id="a number the prompts carry in words",
        ),
        pytest.param(
            {},
            "8: Q 'Could you attach a before-and-after photo of the tile?' -> A 'Here it is.'\n",
            id="a before-and-after photo the owner gave keeps the B-roll",
        ),
    ],
)
def test_a_scene_that_keeps_a_rule_another_way_passes_every_check(
    tmp_path: Path, change: dict[str, object], shop_answers: str
) -> None:
    run = saved_run(tmp_path, [{**GOOD, **change}], shop_answers=shop_answers)

    assert failed(run) == set()


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


def test_an_ad_folder_with_scene_files_but_no_scene_folders_is_skipped(tmp_path: Path) -> None:
    # runs/graded-broll-run/08-planner-a-vs-b keeps scene-2-*.txt files, not scene folders.
    run = saved_run(tmp_path, [GOOD])
    loose = tmp_path / "8-planner-a-vs-b"
    loose.mkdir()
    (loose / "scene-2-plan.txt").write_text("a plan")

    assert {result.ad for result in check_run(run)} == {"8-toilet-cleaner"}


def test_a7_names_the_purposes_the_app_saves_drawn_pictures_under() -> None:
    # A7 tells drawn pictures from photos by the purpose each is saved under: renaming one
    # would fail every good reference-mode scene.
    from evals.rule_checks import _DRAWN_PICTURES
    from gateway.catalog import MODEL_FOR_PURPOSE

    assert all(purpose in MODEL_FOR_PURPOSE for purpose in _DRAWN_PICTURES)
