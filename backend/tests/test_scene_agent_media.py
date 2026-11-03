"""The scene agent retains judge fault words and a bounded picture conversation."""

from typing import Any

from agents.scene_agent import media
from gateway.types import Shown, ToolUse
from jobs import clip_judge

from .test_producer_auto_fix import failing

PRODUCT_FAULT = {
    "product_from_references": "A white mug with a lid.",
    "frames": [],
    "verdict": "fail",
    "compared_against": "Image 2",
    "clip_shows": "a second handle on the back, at 2.3 s",
    "real_product_shows": "one handle, on the right",
    "fault_frame_times": [2.3, 2.5],
    "fault": "The mug grows a second handle.",
    "references_never_show": None,
}


FAILED = {"verdict": "fail", "checks": {"product": "fail"}, "answers": {"product": [PRODUCT_FAULT]}}


FIXED_FORM = (
    "- Compared against Image 2: the clip shows a second handle on the back, at 2.3 s; the "
    "real product shows one handle, on the right (at 2.3 s, 2.5 s). The fault: The mug "
    "grows a second handle."
)


def test_a_product_fault_is_given_in_its_fixed_form() -> None:
    assert FIXED_FORM in media.judge_words(FAILED)


def test_a_detail_the_references_never_show_is_told_as_left_out_of_the_checks() -> None:
    words = media.judge_words({**FAILED, "detail_not_shown": "the mug's base"})

    assert (
        "The judge left out one detail the references never show: the mug's base."
    ) in words.splitlines()


def test_a_clip_failing_product_and_motion_is_given_both_faults() -> None:
    lid = "The lid floats off by itself."
    ruling = clip_judge.judged([PRODUCT_FAULT], [failing(lid)])
    assert ruling["checks"]["product"] == ruling["checks"]["real_motion"] == "fail"

    words = media.judge_words(ruling).splitlines()

    assert words.index(FIXED_FORM) < words.index(f"- It found: {lid}")
    # The PRODUCT fault is said once, in its fixed form only.
    assert not any(str(PRODUCT_FAULT["fault"]) in line for line in words if "It found" in line)


def test_the_library_keeps_half_a_turns_pictures() -> None:
    assert media.LIBRARY_SHARE == 0.5
    assert media._library_room() == media.MOST_PICTURES // 2


def test_a_turn_never_holds_more_than_its_pictures_and_keeps_the_newest_try() -> None:
    def used(tool: str, n: int, tag: str) -> ToolUse:
        return ToolUse(
            call_id=tag,
            tool=tool,
            arguments={},
            result=tag,
            pictures=[Shown(label=f"{tag} {i}", key=f"{tag}-{i}") for i in range(n)],
        )

    conversation: list[Any] = [
        used("look_at_try", 20, "try0"),
        *(used("look_at_library", 3, f"look{i}") for i in range(20)),
        used("film", 15, "try1"),
    ]

    fitted = [each for each in media._fit(conversation) if isinstance(each, ToolUse)]

    assert sum(len(each.pictures) for each in fitted) <= media.MOST_PICTURES
    assert len(fitted[-1].pictures) == 15
    kept = [each for each in fitted if each.tool == "look_at_library" and each.pictures]
    assert sum(len(each.pictures) for each in kept) <= media._library_room()
