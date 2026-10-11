"""How the start-picture judge is measured against Julian's graded pictures: each wrong
picture judged several times and counted as caught only when every run fails it, each good
one judged once and listed with the judge's reasons when it fails."""

from pathlib import Path
from typing import Literal

from evals.calibrate_picture_check import PictureCase, load_cases, report


def case(id: str, label: Literal["pass", "fail"]) -> PictureCase:
    return PictureCase(id=id, label=label, picture=Path(f"{id}.png"), shop_photo=Path("p.png"))


def test_a_wrong_picture_counts_as_caught_only_when_every_run_fails_it() -> None:
    answers = {"phone": [["Screen and camera on one face."]] * 3, "toilet": [["x"], [], []]}

    def judge(picture: PictureCase) -> list[str]:
        return answers[picture.id].pop()

    lines = report([case("phone", "fail"), case("toilet", "fail")], 3, judge)

    assert lines[:3] == [
        "wrong pictures caught every time: 1/2",
        "  phone: caught 3/3",
        "  toilet: caught 1/3",
    ]


def test_a_good_picture_the_judge_fails_is_listed_with_why() -> None:
    def judge(picture: PictureCase) -> list[str]:
        return ["Both knobs on one frame."] * 2 if picture.id == "bag" else []

    lines = report([case("bag", "pass"), case("mug", "pass")], 5, judge)

    assert lines[-2:] == [
        "good pictures failed: 1/2",
        "  WRONGLY FAILED bag: Both knobs on one frame. / Both knobs on one frame.",
    ]


def test_every_graded_case_has_its_picture_and_shop_photo() -> None:
    cases = load_cases()

    assert (len(cases), sum(c.label == "fail" for c in cases)) == (62, 10)
