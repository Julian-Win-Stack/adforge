"""What the fixing agent is given after a judge fail (#229, spec #210 point 22): the judge's
fault in its fixed form, what the failed clip was sent, the library as a text list and the
planner's note on how the product behaves; and the look tool that opens one library item, a
picture or a page video's moment, by number, which stays in the fix's conversation. The
agent, the judge, the pictures and the clips are faked at the gateway."""

from collections.abc import Callable
from typing import Any

import pytest

from adforge import file_store
from agents import fixing_agent
from agents.fixing_agent import Failure, fix_scene
from agents.models import ToolCall
from gateway.fake import FakeModel, turn
from gateway.types import Said, Shown, ToolUse
from jobs import clip_judge
from jobs.clip_judge import ASKS
from jobs.library import (
    LookFrame,
    PictureLabel,
    VideoWatch,
    WatchedMoment,
    add_to_library,
    keep_watch,
    library_of,
    number_moments,
)
from jobs.library_items import library_text
from jobs.models import AgentFix, AgentFixTry, Job, ProducedItem, ProductVideo, SceneStep

from .conftest import HeldSteps, WorkerStopped, picture
from .sent_lists import holding_the_fixture, piece
from .test_fixing_agent import COUNTED, everything_the_agent_read, film_again
from .test_producer_auto_fix import clip_of_scene_2, failing
from .test_producer_broll import checked, instructed  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db(transaction=True)

BEHAVES = "The lid clicks shut and the tea stays hot for hours."
SHUT = "The lid shuts by itself at 3 s."
LOOK_FRAME = PictureLabel(
    kind="state",
    side="front",
    alone="yes",
    colour="white",
    target="",
    label="The mug with its lid open, from the front.",
)


@pytest.fixture
def failed_clip(
    fake_model: FakeModel,
    checked: None,  # noqa: F811
    steps: HeldSteps,
    say: Callable[..., None],
) -> SceneStep:
    """Scene 2's clip, made and judged, its job with a behaviour note, a page
    video watched into two moments and a library whose item 7 is one of its look frames."""
    step = clip_of_scene_2(fake_model, steps, say)
    job = step.scene.job
    Job.objects.filter(pk=job.pk).update(product_behaviour=BEHAVES)
    video = ProductVideo.objects.create(
        job=job,
        position=1,
        kind=ProductVideo.Kind.VIDEO,
        file=file_store.save("page_video_1.mp4", piece(4.0)),
        duration=4.0,
    )
    keep_watch(
        video,
        VideoWatch(
            moments=number_moments(
                [
                    WatchedMoment(
                        start=0, end=1.5, happens="A hand lifts the lid.", on_screen="An open mug."
                    ),
                    WatchedMoment(
                        start=1.5,
                        end=3.5,
                        happens="Tea is poured into the mug.",
                        on_screen="Steam rises from the mug.",
                    ),
                ]
            ),
            look_frames=[1.0],
            colour="white",
            transcript="music only",
        ),
    )
    while len(library_of(job)) < 7:
        add_to_library(
            job,
            file_store.save(f"look_{len(library_of(job))}.png", picture(30, 40, (200, 200, 200))),
            LOOK_FRAME,
            LookFrame(video=1, second=1.0),
        )
    return step


# The judge's PRODUCT check failing the clip, as #228 rules it.
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


def fixed(step: SceneStep) -> fixing_agent.Outcome:
    """The agent fixes the step's scene for the judge's PRODUCT fail."""
    return fix_scene(step, door="judge", failure=Failure(ruling=FAILED), goal=None)


def first_clip(step: SceneStep) -> ProducedItem:
    clip = step.produced.order_by("id").first()
    assert clip is not None
    return clip


def fix_turns(fake_model: FakeModel) -> list[Any]:
    return [each for each in fake_model.turns_taken if each.purpose == "fix_scene"]


def intro(fake_model: FakeModel) -> str:
    said = fix_turns(fake_model)[0].handoff.conversation[0]
    assert isinstance(said, Said)
    return said.text


# --- The first message --------------------------------------------------------------------


def test_after_a_judge_fail_the_first_message_holds_fault_sent_list_library_and_note(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    sent = holding_the_fixture(first_clip(failed_clip))
    fake_model.respond("fix_scene", turn(calls=[film_again()]))

    fixed(failed_clip)

    said = intro(fake_model)
    # The judge's fault, in its fixed form.
    assert "What failed:\nThe judge's verdict: fail." in said and FIXED_FORM in said
    # The sent list: every picture with its label and library number, the piece, the prompt.
    for each in sent.pictures:
        named = (
            f"library item {each.library_number}"
            if each.library_number is not None
            else "the drawn picture of the presenter in the ad's room"
        )
        assert f"Image {each.image}: {named}. {each.label}" in said
    for cut in sent.pieces:
        assert f"Video {cut.number}: page video {cut.video}, moment {cut.moment}," in said
    # Whole lines: a piece trimmed to fit, the left-out picture's reason, a fallen-back recolour.
    assert (
        "Video 2: page video 2, moment 1, 3 s to 13 s, cut from its 3 s to 15 s because the "
        "pieces ran past the video model's 15 s together. only for the movement, not the look"
    ) in said.splitlines()
    assert (
        "Left out: library item 5. The garlic press, closed, seen from the top. (past the video "
        "model's 9 pictures, another side photo)"
    ) in said.splitlines()
    assert "Library item 2 couldn't be made red, so it went as it was." in said.splitlines()
    assert sent.prompt in said
    # The library as a text list, numbered as the job's library is, with no pictures.
    job = failed_clip.scene.job
    listed = said.split("The ad's picture library")[1]
    assert library_text(job) in listed
    numbers = [line.split(".")[0] for line in library_text(job).splitlines()]
    assert (
        numbers == [str(item.number) for item in library_of(job)] == [str(n) for n in range(1, 8)]
    )
    # The page video's moments, which look_at_library opens by number.
    assert "Page video 1, moment 2, 1.5 s to 3.5 s: Tea is poured into the mug." in said
    # The planner's note on how the product behaves.
    assert f"How this product behaves, as the planner wrote it from the page: {BEHAVES}" in said
    # Gone: the start picture's prompt, "Image N = job", a count of tries.
    assert "start picture's prompt" not in said
    assert " = " not in said.split("The page's words:")[0]
    assert not COUNTED.search(said)


def test_a_product_fault_is_given_in_its_fixed_form() -> None:
    assert FIXED_FORM in fixing_agent.judge_words(FAILED)


def test_a_clip_failing_product_and_motion_is_given_both_faults() -> None:
    lid = "The lid floats off by itself."
    ruling = clip_judge.judged([PRODUCT_FAULT], [failing(lid)], first_check="product")
    assert ruling["checks"]["product"] == ruling["checks"]["real_motion"] == "fail"

    words = fixing_agent.judge_words(ruling).splitlines()

    assert words.index(FIXED_FORM) < words.index(f"- It found: {lid}")
    # The PRODUCT fault is said once, in its fixed form only.
    assert not any(str(PRODUCT_FAULT["fault"]) in line for line in words if "It found" in line)


# --- The look tool ------------------------------------------------------------------------

LOOK_AT_7 = ("look_at_library", {"item": 7, "video": None, "moment": None})
LOOK_AT_MOMENT = ("look_at_library", {"item": None, "video": 1, "moment": 2})


def looked(conversation: list[Any]) -> list[ToolUse]:
    return [
        each
        for each in conversation
        if isinstance(each, ToolUse) and each.tool == "look_at_library"
    ]


def test_the_agent_opens_a_look_frame_and_a_moment_and_they_stay_in_its_context(
    fake_model: FakeModel, failed_clip: SceneStep, monkeypatch: pytest.MonkeyPatch
) -> None:
    # So few pictures fit a turn that later looks would push the first ones out: 4 are kept
    # for the library, the item's 1 and the moment's 3.
    monkeypatch.setattr(fixing_agent, "MOST_PICTURES", 8)
    fake_model.respond(
        "fix_scene",
        turn(calls=[LOOK_AT_7]),
        turn(calls=[LOOK_AT_MOMENT]),
        turn(calls=[("look_at_try", {"number": 0})]),
        turn(calls=[("look_at_try", {"number": 0})]),
        turn(calls=[("look_at_place", {})]),
        turn(calls=[film_again()]),
    )

    fixed(failed_clip)

    item = ToolCall.objects.get(tool="look_at_library", arguments__item=7)
    [entry] = [each for each in library_of(failed_clip.scene.job) if each.number == 7]
    assert item.result.startswith("Library item 7.")
    assert [(each["label"], each["key"]) for each in item.pictures] == [
        (f"Library item 7: {entry.label}", entry.file)
    ]
    moment = ToolCall.objects.get(tool="look_at_library", arguments__moment=2)
    assert moment.result.startswith("Page video 1, moment 2, 1.5 s to 3.5 s:")
    assert moment.pictures and all(
        each["label"].startswith("Page video 1, moment 2, Frame") for each in moment.pictures
    )
    # The frames are of the moment's own seconds in the video.
    first_time = float(moment.pictures[0]["label"].split(" at ")[1].split(" s")[0])
    assert 1.5 <= first_time < 3.5
    # Three turns later, and after, both looks still show their pictures.
    turns = fix_turns(fake_model)
    for later in turns[4:]:
        assert (
            sum(
                len(each.pictures)
                for each in later.handoff.conversation
                if isinstance(each, ToolUse)
            )
            <= 8
        )
        kept = looked(later.handoff.conversation)
        assert [[shown.key for shown in each.pictures] for each in kept] == [
            [each["key"] for each in item.pictures],
            [each["key"] for each in moment.pictures],
        ]
    # While an older look at try 0 lost its pictures to the room left.
    last = turns[-1].handoff.conversation
    assert any(
        isinstance(each, ToolUse) and each.tool == "look_at_try" and not each.pictures
        for each in last
    )


def test_a_worker_restart_rebuilds_the_conversation_with_what_the_agent_opened(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    fake_model.respond(
        "fix_scene",
        turn(calls=[LOOK_AT_7]),
        turn(calls=[LOOK_AT_MOMENT]),
        WorkerStopped(),
        turn(calls=[film_again()]),
    )
    with pytest.raises(WorkerStopped):
        fixed(failed_clip)
    before = looked(fix_turns(fake_model)[-1].handoff.conversation)

    fixed(failed_clip)

    after = looked(fix_turns(fake_model)[-1].handoff.conversation)
    assert [each.tool for each in after] == ["look_at_library", "look_at_library"]
    assert after == before and all(each.pictures for each in after)
    assert ToolCall.objects.filter(tool="look_at_library").count() == 2


@pytest.mark.parametrize(
    ("arguments", "refused"),
    [
        ({"item": 99, "video": None, "moment": None}, "there is no library item 99"),
        ({"item": None, "video": 1, "moment": 9}, "page video 1 has no moment 9"),
        ({"item": None, "video": 4, "moment": 1}, "there is no watched page video 4"),
        ({"item": 7, "video": 1, "moment": 2}, "name a library item, or a video and its moment"),
        ({"item": None, "video": 1, "moment": None}, "name a library item, or a video and"),
    ],
)
def test_a_look_at_what_isnt_in_the_library_is_refused(
    fake_model: FakeModel, failed_clip: SceneStep, arguments: dict[str, Any], refused: str
) -> None:
    fake_model.respond(
        "fix_scene", turn(calls=[("look_at_library", arguments)]), turn(calls=[film_again()])
    )
    fixed(failed_clip)
    result = ToolCall.objects.get(tool="look_at_library").result
    assert result.startswith(f"Refused: {refused}")


def test_the_look_tool_and_moments_name_no_count_of_tries(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    fake_model.respond(
        "fix_scene",
        turn(calls=[LOOK_AT_7]),
        turn(calls=[LOOK_AT_MOMENT]),
        turn(calls=[film_again()]),
    )
    fixed(failed_clip)
    read = everything_the_agent_read(fake_model)
    assert [COUNTED.findall(text) for text in read if COUNTED.search(text)] == []


# --- Root cause and move, on every try ----------------------------------------------------


def test_every_try_records_the_agents_root_cause_and_move(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    fake_model.respond("check_clip_motion", *[failing(SHUT)] * ASKS)
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again(root_cause="The prompt never says how the pour ends.")]),
        turn(
            calls=[
                film_again(
                    root_cause="Words alone never showed the lid's movement.",
                    move="Use the page video's pour for the movement.",
                )
            ]
        ),
    )

    fixed(failed_clip)

    assert list(
        AgentFixTry.objects.filter(agent_fix=AgentFix.objects.get())
        .order_by("number")
        .values_list("number", "root_cause", "move")
    ) == [
        (1, "The prompt never says how the pour ends.", film_again()[1]["move"]),
        (
            2,
            "Words alone never showed the lid's movement.",
            "Use the page video's pour for the movement.",
        ),
    ]


# --- The library's room in a turn ---------------------------------------------------------


def test_a_look_opened_again_shows_its_pictures_once(
    fake_model: FakeModel, failed_clip: SceneStep
) -> None:
    fake_model.respond(
        "fix_scene",
        turn(calls=[LOOK_AT_MOMENT]),
        turn(calls=[LOOK_AT_MOMENT]),
        turn(calls=[film_again()]),
    )
    fixed(failed_clip)

    first, second = looked(fix_turns(fake_model)[-1].handoff.conversation)
    assert first.pictures == [] and fixing_agent.SHOWN_LATER in first.result
    assert second.pictures
    assert not ToolCall.objects.filter(
        tool="look_at_library", result__startswith="Refused"
    ).exists()


def test_a_look_past_the_librarys_room_is_refused_in_words_and_the_newest_try_stays(
    fake_model: FakeModel, failed_clip: SceneStep, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 3 of a turn's pictures are the library's: the moment's 3 fit, item 7 then doesn't.
    monkeypatch.setattr(fixing_agent, "LIBRARY_SHARE", 3 / fixing_agent.MOST_PICTURES)
    fake_model.respond("check_clip_motion", *[failing(SHUT)] * ASKS)
    fake_model.respond(
        "fix_scene",
        turn(calls=[film_again()]),
        turn(calls=[LOOK_AT_MOMENT]),
        turn(calls=[LOOK_AT_7]),
        turn(calls=[("give_up", {"reason": "The lid never shuts.", "best_try": 1})]),
    )

    fixed(failed_clip)

    refused = ToolCall.objects.get(tool="look_at_library", arguments__item=7)
    assert refused.result.startswith(f"Refused: {fixing_agent.LIBRARY_FULL}")
    assert not COUNTED.search(fixing_agent.LIBRARY_FULL)
    last = fix_turns(fake_model)[-1].handoff.conversation
    shown = [each for each in last if isinstance(each, ToolUse)]
    assert sum(len(each.pictures) for each in shown) <= fixing_agent.MOST_PICTURES
    # The newest try's pictures, its frames, are still shown beside the open moment.
    newest = [each for each in shown if each.tool == "film_again"][-1]
    assert newest.pictures
    assert looked(last)[0].pictures


def test_the_library_keeps_half_a_turns_pictures() -> None:
    assert fixing_agent.LIBRARY_SHARE == 0.5
    assert fixing_agent._library_room() == fixing_agent.MOST_PICTURES // 2


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
        used("film_again", 15, "try1"),
    ]

    fitted = [each for each in fixing_agent._fit(conversation) if isinstance(each, ToolUse)]

    assert sum(len(each.pictures) for each in fitted) <= fixing_agent.MOST_PICTURES
    assert len(fitted[-1].pictures) == 15
    kept = [each for each in fitted if each.tool == "look_at_library" and each.pictures]
    assert sum(len(each.pictures) for each in kept) <= fixing_agent._library_room()
