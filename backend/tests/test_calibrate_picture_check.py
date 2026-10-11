"""How the start-picture judge is measured against Julian's graded pictures: each wrong
picture judged several times and counted as caught only when every run fails it, each good
one judged once and listed with the judge's reasons when it fails, a picture he never passed
listed for him, and a judgement with an unanswered ask or an error never read as a pass."""

from pathlib import Path
from typing import Literal

from evals.calibrate_picture_check import Judged, PictureCase, load_cases, report


def case(id: str, label: Literal["pass", "fail"], graded_as: str = "passed") -> PictureCase:
    return PictureCase(
        id=id,
        label=label,
        graded_as=graded_as,
        picture=Path(f"{id}.png"),
        shop_photo=Path("p.png"),
    )


def test_a_wrong_picture_counts_as_caught_only_when_every_run_fails_it() -> None:
    answers = {"phone": [["Screen and camera on one face."]] * 3, "toilet": [["x"], [], []]}

    def judge(picture: PictureCase) -> Judged:
        return Judged(answers[picture.id].pop())

    lines = report([case("phone", "fail"), case("toilet", "fail")], 3, judge)

    assert lines[:3] == [
        "wrong pictures caught every time: 1/2",
        "  phone: caught 3/3",
        "  toilet: caught 1/3",
    ]


def test_a_good_picture_the_judge_fails_is_listed_with_why() -> None:
    def judge(picture: PictureCase) -> Judged:
        return Judged(["Both knobs on one frame."] * 2 if picture.id == "bag" else [])

    lines = report([case("bag", "pass"), case("mug", "pass")], 5, judge)

    assert lines[-4:-2] == [
        "good pictures failed: 1/2",
        "  WRONGLY FAILED bag: Both knobs on one frame. / Both knobs on one frame.",
    ]


def test_a_picture_julian_never_passed_is_listed_for_him_not_counted_against_the_judge() -> None:
    def judge(picture: PictureCase) -> Judged:
        return Judged(["Two spouts."] * 2)

    lines = report([case("kettle", "pass", "no_picture_complaint")], 5, judge)

    assert lines[-4:-1] == [
        "good pictures failed: 0/0",
        "pictures with no grade of their own failed: 1/1",
        "  FOR JULIAN kettle: Two spouts. / Two spouts.",
    ]


def test_an_unanswered_ask_or_an_error_is_never_read_as_a_clean_pass() -> None:
    def judge(picture: PictureCase) -> Judged:
        if picture.id == "lost":
            raise OSError("no such picture")
        return Judged([], unchecked=3)

    lines = report([case("down", "pass"), case("lost", "pass")], 5, judge)

    assert lines[-3:] == [
        "judgements not to trust: 2",
        "  down: 3 ask(s) unchecked",
        "  ERROR lost: OSError('no such picture')",
    ]


def test_the_graded_set_holds_62_pictures_10_of_them_wrong() -> None:
    cases = load_cases()

    assert (len(cases), sum(c.label == "fail" for c in cases)) == (62, 10)
