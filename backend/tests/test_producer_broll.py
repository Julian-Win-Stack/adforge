"""A scene that shows the product while the person's voice says its line, rather than the
person saying it to camera: its starting picture, its clip and the finished ad, driven
through the chat. The producer's model and the scene models are faked at the gateway, but
the clips are real tiny videos and ffmpeg runs for real."""

from collections import defaultdict
from collections.abc import Callable
from typing import Any

import pytest

from adforge.file_store import read
from agents import tasks
from gateway.fake import FakeModel, turn
from gateway.models import ModelCall
from gateway.types import ModelReply, ModelRequest
from jobs import work
from jobs.models import Job, ProducedItem, Scene, SceneStep
from jobs.scenes import (
    BEFORE_PICTURE_JOB,
    BROLL_KIND_RULES,
    BROLL_PICTURE_INSTRUCTIONS,
    BROLL_SHARED_RULES,
    broll_prompt_instructions,
)

from .conftest import (
    FACTS_OK,
    NO_CHOICES,
    PLAN,
    HeldSteps,
    WorkerStopped,
    before_picture_file,
    broll,
    colour_at,
    drawn_in,
    handoffs,
    paid_for,
    results_of,
    silences,
    video,
)
from .test_frozen_rules import BROLL_PROMPT as FROZEN_BROLL_PROMPT
from .test_frozen_rules import FROZEN

# Each request commits on its own, as on the real server, and so does the producer's work.
pytestmark = pytest.mark.django_db(transaction=True)

SHOWS = "Hot tea poured into the mug on a workbench."
# Scene 2's line: its 8 words take the fake voice, at 2 a second, 4 seconds to say, the
# least a B-roll line may take.
LINE_2 = "Hand-thrown, holds 350 ml, and dishwasher safe too."

# The mug plan, with its second scene showing the mug rather than the person saying it.
BROLL_PLAN: dict[str, Any] = {
    **PLAN,
    "plan": {
        **PLAN["plan"],
        "scenes": [
            broll({**scene, "line": LINE_2, "shows": SHOWS} if number == 2 else scene)
            for number, scene in enumerate(PLAN["plan"]["scenes"], start=1)
        ],
    },
}

TALKING_CHOICE: dict[str, Any] = {
    "photo": 1,
    "photo_reason": "The front of the mug shows its glaze, which suits the line.",
    "prompt": "She holds the mug up beside her face, handle out, in her sunny workshop.",
    "prompt_reason": "Holding it up by her face introduces the mug.",
}

MOTION = "Steam rises as the tea fills the mug; the camera holds still."

BROLL_CHOICE: dict[str, Any] = {
    "photo": 1,
    "photo_reason": "The front of the mug shows its glaze as the tea is poured.",
    "prompt": "The mug on a workbench in a sunny workshop, tea being poured into it.",
    "prompt_reason": "It shows the pour the line describes, in the person's workshop.",
    "motion_prompt": MOTION,
    "motion_prompt_reason": "The pour and the steam are what moves in the scene.",
}

# Scene 2's labels when it shows the mug doing a job rather than at its best, as planned.
DOES_A_JOB: dict[str, Any] = {
    "broll_kind": "does a job",
    "person_shown": "no face",
    "usage": "Hot tea is poured into the mug from a teapot.",
    "result": "The mug is full of steaming tea.",
    "needs": [],
}


@pytest.fixture
def checked(fake_model: FakeModel, page_read: str, say: Callable[..., None]) -> None:
    """A chat whose ad is planned with scene 2 showing the mug, has its person, and whose
    three scenes passed the fact check."""
    fake_model.respond("produce", turn(calls=[("plan_ad", {})]), turn(says="Here's the plan."))
    fake_model.respond("plan_ad", BROLL_PLAN)
    say("Plan it")
    fake_model.respond(
        "produce",
        turn(calls=[("create_person", {})]),
        turn(calls=[("run_planning_checks", NO_CHOICES)]),
        turn(says="The script is checked."),
    )
    fake_model.respond("fact_check", FACTS_OK)
    say("Make the person and check the script")
    assert Scene.objects.get(number=2).shows == SHOWS


@pytest.fixture
def instructed(fake_model: FakeModel, monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """What each model the fake stands in for was told to do, by its purpose, in turn."""
    told: defaultdict[str, list[str]] = defaultdict(list)
    answer = fake_model.complete

    def complete(request: ModelRequest[Any]) -> ModelReply[Any]:
        told[request.purpose].append(request.instructions)
        return answer(request)

    monkeypatch.setattr(fake_model, "complete", complete)
    return told


def run(fake_model: FakeModel, steps: HeldSteps) -> None:
    """Run the held steps, the producer replying once to each."""
    fake_model.respond("produce", *[turn(says="Done.") for _ in steps.held])
    steps.run_held()


def calling(
    fake_model: FakeModel, say: Callable[..., None], *calls: tuple[str, dict[str, Any]]
) -> None:
    """Have the producer make these calls, then reply."""
    fake_model.respond("produce", turn(calls=list(calls)), turn(says="On it."))
    say("Go on")


def picture_of(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], scene: int
) -> None:
    """Make `scene`'s starting picture: scene 2 shows the mug, the others the person."""
    calling(fake_model, say, ("make_starting_picture", {"scene": scene, "note": None}))
    if scene == 2:
        fake_model.respond("choose_broll_picture", BROLL_CHOICE)
    else:
        fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    run(fake_model, steps)


def made_ready(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], scenes: tuple[int, ...]
) -> None:
    """Make each of `scenes`' starting picture, audio and transcript."""
    calling(
        fake_model,
        say,
        *[("make_starting_picture", {"scene": scene, "note": None}) for scene in scenes],
        *[("make_line_audio", {"scene": scene}) for scene in scenes],
    )
    for scene in scenes:
        if scene == 2:
            fake_model.respond("choose_broll_picture", BROLL_CHOICE)
        else:
            fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    run(fake_model, steps)
    calling(fake_model, say, *[("transcribe_line_audio", {"scene": scene}) for scene in scenes])
    run(fake_model, steps)


@pytest.fixture
def ready(fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]) -> None:
    """A chat whose scene 2 has its starting picture, and its audio, transcribed."""
    made_ready(fake_model, steps, say, (2,))


def clip_of_scene_2(fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]) -> None:
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    run(fake_model, steps)


def picture_of_scene_2(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None], *choices: dict[str, Any]
) -> SceneStep:
    """Scene 2's latest starting picture step, made with the prompts in `choices`, one for each
    draw (the usual prompts when none are given)."""
    calling(fake_model, say, ("make_starting_picture", {"scene": 2, "note": None}))
    fake_model.respond("choose_broll_picture", *(choices or (BROLL_CHOICE,)))
    run(fake_model, steps)
    return SceneStep.objects.filter(kind="starting_picture", scene__number=2).last()  # type: ignore[return-value]


# Every B-roll clip's prompt starts with this, in reference mode (Julian 2026-10-16 19:14).
IMAGE_1 = "Image 1 is how the clip starts: the clip starts exactly like Image 1. "


def clips_asked(field: str) -> list[Any]:
    """`field` of what the video model was asked for, for each clip, oldest first."""
    return [handoff[field] for handoff in handoffs("make_broll_clip")]


def kept_clips() -> list[bytes]:
    """Each clip kept, oldest first."""
    return [read(clip.file) for clip in ProducedItem.objects.filter(kind="clip").order_by("id")]


# --- The whole ad ------------------------------------------------------------------------------


@pytest.fixture
def assembled(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> ProducedItem:
    """The finished ad, made through the chat, with scene 2 showing the mug."""
    return assemble_through_the_chat(fake_model, steps, say)


def assemble_through_the_chat(
    fake_model: FakeModel, steps: HeldSteps, say: Callable[..., None]
) -> ProducedItem:
    calling(fake_model, say, ("create_music", {"mood": "light upbeat lo-fi"}))
    made_ready(fake_model, steps, say, (1, 2, 3))
    calling(fake_model, say, *[("make_clip", {"scene": scene}) for scene in (1, 2, 3)])
    run(fake_model, steps)
    calling(fake_model, say, ("assemble_ad", {}))
    return Job.objects.get().produced.get(kind="finished_ad")


def test_a_broll_scene_planned_before_it_had_labels_still_makes_its_ad(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # A job planned before the plan gave each B-roll scene its kind, person, usage, result
    # and needs has them blank.
    Scene.objects.update(broll_kind="", person_shown="", usage="", result="", needs=[])

    ad = assemble_through_the_chat(fake_model, steps, say)

    assert [cut["scene"] for cut in ad.cuts] == [1, 2, 3]
    assert len(clips_asked("prompt")) == 1


def test_a_scene_that_shows_the_product_plays_its_whole_clip(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # A second of silence before and after every line, as a real voice leaves.
    fake_model.pause_seconds = 1.0

    ad = assemble_through_the_chat(fake_model, steps, say)

    # Scene 2's audio, 4 seconds of words and 2 of silence, is covered by a 6-second clip,
    # and all of it is kept, so its motion plays out. The talking scenes around it are cut
    # to their words: the clip ends with its audio, so scene 3 skips none of its picture.
    assert [(cut["scene"], cut["clip_start"], cut["clip_end"]) for cut in ad.cuts] == [
        (1, 0.9, 5.1),
        (2, 0.0, 6.0),
        (3, 0.9, 3.1),
    ]
    assert colour_at(read(ad.file), 9.5) == "lime"


def test_a_scene_that_shows_the_product_has_its_words_captioned(assembled: ProducedItem) -> None:
    assert [c["text"] for c in assembled.captions if 4.0 <= c["start"] < 8.0] == [
        "Hand-thrown, holds 350",
        "ml, and dishwasher",
        "safe too.",
    ]
    assert drawn_in(read(assembled.file), 5.75) == {"bottom"}


# --- The starting picture ----------------------------------------------------------------------


def test_the_picture_is_planned_from_what_the_scene_shows(ready: None) -> None:
    (planned,) = handoffs("choose_broll_picture")
    assert (planned["scene"], planned["line"], planned["shows"]) == (
        2,
        LINE_2,
        "Hot tea poured into the mug on a workbench.",
    )


def test_the_picture_is_planned_from_the_scenes_broll_labels(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    Scene.objects.filter(number=2).update(**DOES_A_JOB)

    picture_of(fake_model, steps, say, 2)

    (planned,) = handoffs("choose_broll_picture")
    told = ("broll_kind", "person_shown", "usage", "result")
    assert {field: planned[field] for field in told} == {field: DOES_A_JOB[field] for field in told}
    # What each picture the picture model gets is for, in the order it gets them.
    assert planned["pictures"] == [
        {"image": 1, "job": "the product, only how it looks"},
        {"image": 2, "job": BEFORE_PICTURE_JOB},
    ]


@pytest.mark.parametrize(
    ("scene", "planner", "what_it_shows"),
    [
        pytest.param(
            1,
            "choose_starting_picture",
            "You plan the starting picture for one scene of a short vertical video ad. In the "
            "scene, the person in the portrait holds the product and says the scene's line to "
            "camera. The picture is made by a picture model from two pictures: the portrait "
            "first, then one product photo. A video model then animates it to say the line, so "
            "it is the scene's first frame.",
            id="the person talking",
        ),
        pytest.param(
            2,
            "choose_broll_picture",
            "You write the prompts for one B-roll scene of a short vertical video ad. The scene "
            'doesn\'t show the person talking to camera: it shows what "shows" describes, while '
            "the presenter's voice says the scene's line over it.",
            id="the product",
        ),
    ],
)
def test_the_model_planning_a_picture_is_told_what_the_scene_shows(
    fake_model: FakeModel,
    instructed: dict[str, list[str]],
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
    scene: int,
    planner: str,
    what_it_shows: str,
) -> None:
    picture_of(fake_model, steps, say, scene)

    (told,) = instructed[planner]
    assert told.splitlines()[0] == what_it_shows


def test_the_model_planning_the_picture_is_given_only_its_kinds_rules(
    fake_model: FakeModel,
    instructed: dict[str, list[str]],
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    """The kind stored on the scene picks its rules. That every kind gets the shared rules,
    its own and no other's is checked for each kind without a chat in
    test_producer_clip_from_note.py."""
    Scene.objects.filter(number=2).update(**DOES_A_JOB)

    picture_of(fake_model, steps, say, 2)

    (told,) = instructed["choose_broll_picture"]
    assert BROLL_SHARED_RULES in told
    assert BROLL_KIND_RULES["does a job"] in told
    assert BROLL_KIND_RULES["showcase"] not in told


@pytest.mark.parametrize(
    "rule",
    [
        "One continuous shot",
        # Since 09 Oct "shows", not the line: a line that can't be filmed is said over
        # another B-roll, never acted out (test ads, 08 Oct; Julian 23:40).
        'The product, or the tool used with it, does what "shows" describes',
        'the way "usage" says',
        "in the middle of the frame",
        "Say nothing about the top or the bottom of the frame",
        "Only the presenter is shown",
        "Any hand is fine",
        "The product is shown, not described",
        'Never write "no speech", "no sound" or "no text"',
        "only the product",
        "the setting is described in words",
        'the "before"',
    ],
)
def test_the_model_planning_the_picture_is_given_the_shared_prompt_rules(rule: str) -> None:
    assert rule in BROLL_SHARED_RULES


# Rules Julian's grades proved
# (decisions/agreed-vs-built-2026-10-08.md rows 1, 5, 7, 10, 17). The sentences frozen word
# for word (no timings, one movement where the product does the work, a person named in
# "shows" is in the clip, never the camera, never turn or flip, never the fiddly change, the
# product may stand, no contradictions) are pinned in test_frozen_rules.py; here are the rest.
@pytest.mark.parametrize(
    "rule",
    [
        # Only the scrub, with the voice carrying squeeze and flush, was PERFECT (#8 5 s);
        # "flows as one" let clamp + rotate + glide through and Boreal skipped the middle
        # (N5 s2), so Julian made it one movement, no exceptions.
        "Film one movement only",
        '"usage" tells how it is held and used, not how many steps to film',
        # A plan "shows" of holding up the phone after its drop was overridden back to the
        # drop by the line's claim, 4 runs of 4 (#12, compare.md cause check), so "shows"
        # decides which movement.
        '"shows" says which: when it names one movement, film that one, even when the line',
        # Test ads (08 Oct), N5 Le Duo: the turn was moved before the glide, so the clip filmed
        # the straightening how-to under a line about curls.
        'A part that "shows" or "usage" says happens while the movement is done, such as '
        "pressing down while turning a cap, is part of that one movement: film it during the "
        "movement, never before it or in the starting picture.",
        # Code writes the clip's look and length first (broll_video_prompt).
        "Code starts the video prompt with the clip's length",
    ],
)
def test_the_model_writing_the_prompts_is_told_the_proven_rules(rule: str) -> None:
    assert rule in BROLL_SHARED_RULES


@pytest.mark.parametrize(
    "dropped",
    [
        "One action every 2 to 3 seconds",
        "not standing idle beside the action",
        # The API already sends the shape; the video prompt doesn't repeat it.
        "Upright 9:16",
        "flow as one",
        # Test ads (08 Oct): every B-roll looked phone-filmed; Julian: "there is no extra
        # benefit". A handheld look also moves the camera, against "never the camera".
        "phone",
        "casual",
        "never call the camera fixed",
        # A labelled scene's prompts are no longer planned from "shows" alone: the rules say
        # what is made up and what isn't. The unlabelled instructions keep the sentence.
        "Make nothing up",
    ],
)
def test_the_model_writing_the_prompts_is_no_longer_told_the_rules_grades_rejected(
    dropped: str,
) -> None:
    # Absent from the whole text the model is told, whichever kind the scene is: a rejected
    # rule can't come back through a kind's own rules.
    for kind in BROLL_KIND_RULES:
        assert dropped not in broll_prompt_instructions(kind), kind


# The frozen sentences of the B-roll prompt (test_frozen_rules.py) are shared rules, so each
# is told for every kind, not only the "does a job" kind that file reads.
FROZEN_SHARED_RULES = [
    pytest.param(str(sentence), id=frozen.id)
    for frozen in FROZEN
    for prompt, sentence in [frozen.values]
    if prompt == FROZEN_BROLL_PROMPT
]


@pytest.mark.parametrize("sentence", FROZEN_SHARED_RULES)
@pytest.mark.parametrize("kind", BROLL_KIND_RULES)
def test_every_kind_of_b_roll_scene_is_told_each_frozen_shared_rule(
    kind: str, sentence: str
) -> None:
    assert sentence in broll_prompt_instructions(kind)


# What both best start pictures did (#8 toilet 5 s, #12 V2): rows 9, 18, 31. The sentence
# saying what must be right (camera, parts, counts, hands) is pinned in test_frozen_rules.py.
@pytest.mark.parametrize(
    "rule",
    [
        'The picture prompt opens: "An upright 9:16 photo in a real, ordinary <place>."',
        "say the pose once and what it does to the product's shape",
    ],
)
def test_the_picture_prompt_shows_what_must_be_right(rule: str) -> None:
    assert rule in BROLL_SHARED_RULES


def test_a_scene_showing_the_problem_shows_it_plainly_and_never_changes_it() -> None:
    # Test ads (08 Oct), N3: the shower's "builds up" line was talking; Julian: "As a B-roll
    # scene." Clips are real-time, so the dirt is shown as it is, not building up.
    rules = BROLL_KIND_RULES["shows the problem"]
    assert "plainly, in the starting picture, with the product in view" in rules
    assert "Nothing about the problem changes in the clip." in rules
    # Round 2 (Julian 21:22 "yes"): the example was a filler action.
    assert "set down beside it" not in rules


def test_a_scene_showing_the_problem_has_nobody_touching_the_product() -> None:
    # Round 3 #8 s3 (Julian 2026-10-10 05:30, FAIL): "the one movement is a hand or the
    # product" put a hand on the bottle with nothing real to do, and the prompt asked the
    # lid contact to ripple the bowl water, so the video model invented a pour ("It looks
    # like water ... the lip is not being opened"). s2 (PASS) had nobody touching the bottle
    # and only the water moving.
    rules = BROLL_KIND_RULES["shows the problem"]
    assert (
        "Nobody touches the product: no hand holds it in the starting picture or the clip, "
        "and it stands still, label to the camera, the whole time."
    ) in rules
    assert (
        "The one movement is something natural already in the scene, such as water rippling, "
        "steam rising or a drip falling, and it never comes from the product or does anything "
        "to the problem."
    ) in rules
    assert "The one movement is a hand or the product" not in rules


def test_a_does_a_job_scene_ends_on_its_result() -> None:
    assert "ends on the result" in BROLL_KIND_RULES["does a job"]


def test_a_does_a_job_scene_films_its_result_happening() -> None:
    # Round 2 #8 s4: the bottle beside a clean toilet. Julian: "there is no evidence that the
    # toilet is so clean right now ... we have to show."
    assert (
        "A result is filmed happening, from before to after in the same clip. A clean or "
        "finished thing with the product beside it, with no change filmed, never shows a "
        "result."
    ) in BROLL_KIND_RULES["does a job"]


def test_a_drop_starts_at_the_height_the_page_states() -> None:
    # Round 2 #12 s5: dropped from just above the floor; Julian: "dropping at a really close
    # position from the floor". The shop's photo says "6 ft. drop tested". No hardcoding:
    # with no height stated, a standing person's hand.
    assert (
        "When the action is a drop, fall or throw, the starting picture shows the height it "
        "starts from: the height the page or its photos state, such as 6 ft above the floor; "
        "when none is stated, held in a standing person's hand."
    ) in BROLL_SHARED_RULES
    # Round 2 #12 s5's own picture prompt put "the camera at knee height", so the hand was
    # drawn just above the floor; 15 redraws without that never copied the low drop photo.
    assert (
        "The camera is placed to see the whole fall, from that height down to where it lands: "
        "never at knee height or close to the floor."
    ) in BROLL_SHARED_RULES


def test_a_does_a_job_scene_never_films_putting_the_product_on() -> None:
    # The toilet clip's whole ring vanished where nothing touched it, "time-compressed", after
    # filming the gel go on; Julian: "just scrub it and then that scrub area become clean",
    # "you don't need to even put in the gel at all"
    # (decisions/2026-10-08-result-only-where-scrubbed.md). The result-only-where-touched
    # sentence is pinned in test_frozen_rules.py.
    assert "putting the product on is never filmed or drawn" in BROLL_KIND_RULES["does a job"]


def test_a_showcase_scene_ends_on_what_it_shows() -> None:
    showcase = BROLL_KIND_RULES["showcase"]
    assert 'ends on the moment that shows what "shows" describes, done by a hand' in showcase
    # Round 2: "picked up or set down" made filler B-roll, and every one failed (#12 s2, #8
    # s4, N3). The clutch carry was graded PERFECT (#5), so wearing it as claimed stays.
    assert "picked up or set down" not in showcase
    assert (
        "This scene proves its line by the product being used or worn the way the line "
        "claims, such as a bag carried as a clutch while walking. A hand only holding, "
        "placing, setting down, standing up or pointing at the product never proves anything: "
        "never film that."
    ) in showcase
    # The forced ending made pointless zooms (#15, #4): "a lot better" without it.
    assert "at its best" not in showcase
    assert "camera move" not in showcase


def test_a_scene_with_no_face_is_planned_from_the_photos_alone(ready: None) -> None:
    job = Job.objects.get()
    # Every product photo, Photo 2 too, which shows the mug in cream while the ad's colour is
    # sage green: the writer is told which are in the ad's colour (colour_photos).
    assert ModelCall.objects.get(purpose="choose_broll_picture").images == [
        {"label": "Photo 1", "key": job.photos.get(position=1).file},
        {"label": "Photo 2", "key": job.photos.get(position=2).file},
        {"label": "The before-picture", "key": before_picture_file()},
    ]
    (planned,) = handoffs("choose_broll_picture")
    assert planned["colour_photos"] == [1]


def test_a_scene_with_no_face_sends_the_image_maker_the_main_photo_alone(ready: None) -> None:
    (asked,) = handoffs("make_starting_picture")
    assert asked["pictures"] == [
        Job.objects.get().photos.get(position=1).file,
        before_picture_file(),
    ]


@pytest.fixture
def with_a_face(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    """Scene 2, showing the presenter's face, with its starting picture, audio and
    transcript made."""
    Scene.objects.filter(number=2).update(person_shown="has face")
    made_ready(fake_model, steps, say, (2,))


def test_a_scene_with_a_face_sends_the_image_maker_the_main_photo_then_the_portrait(
    with_a_face: None,
) -> None:
    job = Job.objects.get()
    (asked,) = handoffs("make_starting_picture")
    assert asked["pictures"] == [
        job.photos.get(position=1).file,
        job.produced.get(kind="portrait").file,
        before_picture_file(),
    ]


def test_a_scene_with_a_face_is_planned_from_the_photos_and_the_portrait(
    with_a_face: None,
) -> None:
    job = Job.objects.get()
    assert ModelCall.objects.get(purpose="choose_broll_picture").images == [
        {"label": "Photo 1", "key": job.photos.get(position=1).file},
        {"label": "Photo 2", "key": job.photos.get(position=2).file},
        {"label": "The portrait", "key": job.produced.get(kind="portrait").file},
        {"label": "The before-picture", "key": before_picture_file()},
    ]
    (planned,) = handoffs("choose_broll_picture")
    assert planned["pictures"] == [
        {"image": 1, "job": "the product, only how it looks"},
        {"image": 2, "job": "the presenter"},
        {"image": 3, "job": BEFORE_PICTURE_JOB},
    ]


def test_the_main_photo_is_the_one_the_picture_chooser_picks(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    job = Job.objects.get()
    # Both photos show the mug in the ad's colour, and the chooser picks the second.
    job.photos.update(shows_product_colour=True)
    calling(fake_model, say, ("make_starting_picture", {"scene": 2, "note": None}))
    fake_model.respond("choose_broll_picture", {**BROLL_CHOICE, "photo": 2})
    run(fake_model, steps)

    (asked,) = handoffs("make_starting_picture")
    assert asked["pictures"] == [job.photos.get(position=2).file, before_picture_file()]
    assert SceneStep.objects.get(kind="starting_picture").photo == job.photos.get(position=2)


def test_the_picture_step_stores_its_way_its_pictures_and_both_prompts(
    with_a_face: None,
) -> None:
    step = SceneStep.objects.get(kind="starting_picture")
    assert (step.way, step.pictures_sent, step.prompt, step.motion_prompt) == (
        1,
        [
            {"image": 1, "photo": 1, "job": "the product, only how it looks"},
            {"image": 2, "portrait": True, "job": "the presenter"},
            {"image": 3, "before_picture": True, "job": BEFORE_PICTURE_JOB},
        ],
        BROLL_CHOICE["prompt"],
        MOTION,
    )
    assert (step.photo_reason, step.prompt_reason) == (
        BROLL_CHOICE["photo_reason"],
        BROLL_CHOICE["prompt_reason"],
    )


def test_the_picture_is_asked_for_as_the_model_wrote_it(ready: None) -> None:
    (asked,) = handoffs("make_starting_picture")
    # Nothing is added: the real photo of the product is what keeps it true.
    assert asked["prompt"] == BROLL_CHOICE["prompt"]


# A scene planned before the plan gave B-roll scenes their labels is made as before.


@pytest.fixture
def unlabelled(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    Scene.objects.update(broll_kind="", person_shown="", usage="", result="", needs=[])
    made_ready(fake_model, steps, say, (2,))


def test_an_unlabelled_scene_is_planned_as_before(
    fake_model: FakeModel,
    instructed: dict[str, list[str]],
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
) -> None:
    Scene.objects.update(broll_kind="", person_shown="", usage="", result="", needs=[])

    picture_of(fake_model, steps, say, 2)

    (told,) = instructed["choose_broll_picture"]
    assert told == BROLL_PICTURE_INSTRUCTIONS


def test_an_unlabelled_scenes_picture_is_made_as_before(unlabelled: None) -> None:
    job = Job.objects.get()
    (asked,) = handoffs("make_starting_picture")
    assert asked == {
        "prompt": (
            "The mug on a workbench in a sunny workshop, tea being poured into it. Show only "
            "what is described; add or change nothing about the product."
        ),
        "pictures": [job.produced.get(kind="portrait").file, job.photos.get(position=1).file],
    }
    step = SceneStep.objects.get(kind="starting_picture")
    assert (step.way, step.pictures_sent) == (None, [])


def test_with_reference_mode_switched_off_a_clip_is_made_from_its_starting_picture_as_before(
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The one-line switch back (Julian 2026-10-16 19:08): the starting picture is the clip's
    # first frame and its prompt has no Image 1 sentence, exactly as before.
    monkeypatch.setattr(work, "EVERY_BROLL_CLIP_IN_REFERENCE_MODE", False)
    made_ready(fake_model, steps, say, (2,))
    clip_of_scene_2(fake_model, steps, say)
    picture = ProducedItem.objects.get(kind="starting_picture", scene__number=2)
    assert (clips_asked("starting_picture"), clips_asked("example_pictures")) == (
        [picture.file],
        [[]],
    )
    assert clips_asked("prompt") == [
        "A 5-second video at real-time speed. The camera stays still. Whatever side faces the "
        f"camera at the start stays toward the camera the whole time. {MOTION}"
    ]


def test_an_unlabelled_scenes_clip_is_asked_for_as_before(
    fake_model: FakeModel, unlabelled: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    clip_of_scene_2(fake_model, steps, say)

    assert clips_asked("prompt") == [
        f"{IMAGE_1}Steam rises as the tea fills the mug; the camera holds still. Show only what is "
        "described; add or change nothing about the product."
    ]


def test_a_picture_planned_before_the_worker_stopped_isnt_paid_for_again(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    calling(fake_model, say, ("make_starting_picture", {"scene": 2, "note": None}))
    (step_id,) = steps.held
    # A second plan, were the first wrongly paid for again.
    fake_model.respond("choose_broll_picture", BROLL_CHOICE, BROLL_CHOICE)
    # The worker stops once the plan is paid for, as the picture is made.
    fake_model.respond("make_starting_picture", WorkerStopped())
    with pytest.raises(WorkerStopped):
        steps.run_next()
    fake_model.respond("produce", turn(says="Ready!"))

    tasks.run_scene_step(step_id)

    assert [p for p in paid_for() if p in ("choose_broll_picture", "make_starting_picture")] == [
        "choose_broll_picture",
        "make_starting_picture",
    ]


def test_a_picture_made_before_the_scene_changed_what_it_shows_gets_no_clip(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # The shop owner chose to have the person say scene 2 instead.
    scene = Scene.objects.get(number=2)
    scene.change_line(scene.line, shows="")
    scene.save()

    clip_of_scene_2(fake_model, steps, say)

    assert list(ModelCall.objects.filter(purpose="make_broll_clip")) == []
    assert results_of("make_clip") == [
        "Refused: scene 2's starting picture was made for an earlier line, or for what the "
        "scene showed before, and the scene has changed since. Make its starting picture "
        "again first. Nothing was done."
    ]


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"broll_kind": "does a job", "result": "The mug is full."}, id="kind"),
        pytest.param({"person_shown": "has face"}, id="person"),
        pytest.param({"usage": "Pour the tea in slowly."}, id="usage"),
        pytest.param({"result": "Steam rises from the full mug."}, id="result"),
        pytest.param({"needs": [{"what": "the tea", "photos": [2]}]}, id="needs"),
    ],
)
def test_a_picture_made_before_the_scenes_broll_labels_changed_gets_no_clip(
    fake_model: FakeModel,
    ready: None,
    steps: HeldSteps,
    say: Callable[..., None],
    change: dict[str, Any],
) -> None:
    Scene.objects.filter(number=2).update(**change)

    clip_of_scene_2(fake_model, steps, say)

    assert list(ModelCall.objects.filter(purpose="make_broll_clip")) == []
    assert results_of("make_clip") == [
        "Refused: scene 2's starting picture was made for an earlier line, or for what the "
        "scene showed before, and the scene has changed since. Make its starting picture "
        "again first. Nothing was done."
    ]


def test_a_picture_made_before_the_scenes_broll_labels_changed_is_made_again(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    Scene.objects.filter(number=2).update(usage="Pour the tea in slowly.")

    picture_of(fake_model, steps, say, 2)

    assert paid_for().count("make_starting_picture") == 2
    latest = SceneStep.objects.filter(kind="starting_picture").last()
    assert latest is not None and latest.usage == "Pour the tea in slowly."


def test_a_scene_that_no_longer_shows_the_product_gets_a_picture_of_the_person_talking(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    scene = Scene.objects.get(number=2)
    scene.change_line(scene.line, shows="")
    scene.save()

    calling(fake_model, say, ("make_starting_picture", {"scene": 2, "note": None}))
    fake_model.respond("choose_starting_picture", TALKING_CHOICE)
    run(fake_model, steps)

    # Planned again as the person saying it: the talking prompt, with nothing added.
    assert handoffs("make_starting_picture")[-1]["prompt"] == (
        "She holds the mug up beside her face, handle out, in her sunny workshop."
    )


# --- The clip ----------------------------------------------------------------------------------


def test_the_talking_scenes_clips_are_still_asked_of_heygen(assembled: ProducedItem) -> None:
    assert [
        (call.purpose, call.model)
        for call in ModelCall.objects.filter(purpose__endswith="_clip").order_by("created_at")
        if call.purpose.startswith("make_")
    ] == [
        ("make_talking_clip", "heygen/avatar-iv"),
        ("make_broll_clip", "creatify/boreal-h3"),
        ("make_talking_clip", "heygen/avatar-iv"),
    ]
    # Each says its line, as before.
    assert [sorted(handoff) for handoff in handoffs("make_talking_clip")] == [
        ["audio", "motion_prompt", "picture", "seconds"]
    ] * 2


@pytest.mark.parametrize(
    ("said_in", "asked_for"),
    [
        # Boreal-H3 makes 5 to 15 whole seconds: the fewest that cover the line, at least 5.
        # The rule itself, more lengths included, is checked without a chat in
        # test_producer_fixes.py (test_a_broll_clip_is_estimated_at_its_whole_seconds...).
        pytest.param(4.2, 5, id="4.2 seconds of audio in a 5-second clip"),
        pytest.param(6.3, 7, id="6.3 seconds of audio in a 7-second clip"),
    ],
)
def test_the_clip_is_asked_for_at_the_whole_seconds_that_cover_its_line_and_kept_whole(
    fake_model: FakeModel,
    checked: None,
    steps: HeldSteps,
    say: Callable[..., None],
    said_in: float,
    asked_for: int,
) -> None:
    # Scene 2's 8 words, said at whatever pace takes this long.
    fake_model.words_per_second = 8 / said_in
    made_ready(fake_model, steps, say, (2,))

    clip_of_scene_2(fake_model, steps, say)

    assert clips_asked("seconds") == [asked_for]
    # The clip's real length and a still camera come first, whatever the model wrote, then
    # its prompt as written: Boreal-H3 is sent no sections. No look: the video model's choice
    # (test ads, 08 Oct: every B-roll looked phone-filmed). The side facing the camera stays
    # toward it (round 3, 10 Oct: the phone case flipped mid-clip).
    assert clips_asked("prompt") == [
        f"{IMAGE_1}A {asked_for}-second video at real-time speed. The camera stays still. "
        "Whatever side faces the camera at the start stays toward the camera the whole time. "
        f"{MOTION}"
    ]
    # Kept whole, at the length the video model made it.
    (clip,) = ProducedItem.objects.filter(kind="clip")
    assert (clip.seconds, round(video(read(clip.file))[2], 1)) == (asked_for, asked_for)
    # The video model's clip is silent: the voice is heard from its start to the end of
    # the line, then it is silent to its end (give or take a frame of sound).
    ((silent_from, silent_to),) = silences(read(clip.file))
    assert (silent_from, round(silent_to)) == (said_in, asked_for)
    # The line's audio keeps its own length.
    assert clip.made_from is not None and clip.made_from.seconds == said_in


def test_a_clip_as_long_as_its_line_carries_its_voice_throughout(
    fake_model: FakeModel, checked: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # 10 words at the fake voice's 2 a second: the 5-second clip comes back just as long.
    Scene.objects.filter(number=2).update(
        line="Tea fills the mug, and its glaze shines right through."
    )
    made_ready(fake_model, steps, say, (2,))

    clip_of_scene_2(fake_model, steps, say)

    assert [silences(clip) for clip in kept_clips()] == [[]]


def test_a_clip_shorter_than_its_line_fails_its_step(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    # Asked for 5 seconds, it comes back 3.
    fake_model.clips_short_by = 2.0

    clip_of_scene_2(fake_model, steps, say)

    step = SceneStep.objects.get(kind="clip")
    assert (step.status, step.reason) == (
        "failed",
        "the video model couldn't make the clip (it came back 3 seconds long, shorter than "
        "the line's 4 seconds of audio).",
    )


def test_a_clip_that_came_back_shorter_than_its_line_is_asked_for_afresh(
    fake_model: FakeModel, ready: None, steps: HeldSteps, say: Callable[..., None]
) -> None:
    fake_model.clips_short_by = 2.0
    clip_of_scene_2(fake_model, steps, say)
    fake_model.clips_short_by = 0

    clip_of_scene_2(fake_model, steps, say)

    # Waiting on the short one, red, would only fail again: the one kept is the next, lime.
    assert [colour_at(clip, 1.0) for clip in kept_clips()] == ["lime"]


def the_worker_stops(*_: object, **__: object) -> None:
    raise WorkerStopped


@pytest.fixture
def restarted(
    fake_model: FakeModel,
    ready: None,
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scene 2's clip step, run again after its worker stopped once the clip was fetched and
    the voice laid over it, as it was being kept."""
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    (step_id,) = steps.held
    with monkeypatch.context() as stopping:
        stopping.setattr(ProducedItem.objects, "create", the_worker_stops)
        with pytest.raises(WorkerStopped):
            steps.run_next()
    fake_model.respond("produce", turn(says="Ready!"))
    tasks.run_scene_step(step_id)


def test_a_clip_fetched_before_the_worker_stopped_isnt_paid_for_again(restarted: None) -> None:
    assert [purpose for purpose in paid_for() if purpose.endswith("_clip")] == [
        "make_broll_clip",
        "collect_broll_clip",
    ]


def test_a_clip_fetched_before_the_worker_stopped_still_gets_its_voice(restarted: None) -> None:
    # Scene 2's 4 seconds of line, then silence to the end of its 5-second clip.
    ((clip,),) = [silences(clip) for clip in kept_clips()]
    assert (clip[0], round(clip[1])) == (4.0, 5)


def test_a_clip_asked_of_the_old_boreal_before_the_switch_fails_once_collected(
    fake_model: FakeModel,
    ready: None,
    steps: HeldSteps,
    say: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calling(fake_model, say, ("make_clip", {"scene": 2}))
    (step_id,) = steps.held
    # The worker stops once the clip is asked for, before it is collected.
    with monkeypatch.context() as stopping:
        stopping.setattr(work, "collect_clip", the_worker_stops)
        with pytest.raises(WorkerStopped):
            steps.run_next()
    # It was asked of the old Boreal on fal, before Boreal-H3 replaced it.
    ModelCall.objects.filter(purpose="make_broll_clip").update(
        model="creatify/boreal",
        provider="fal",
        output={"video_id": "fal-request-1"},
    )
    fake_model.respond("produce", turn(says="It failed."))

    tasks.run_scene_step(step_id)

    step = SceneStep.objects.get(pk=step_id)
    assert (step.status, step.reason) == (
        "failed",
        "the video model couldn't make the clip (it was asked of creatify/boreal, a video "
        "model no longer used, so it can't be collected).",
    )
    assert paid_for().count("make_broll_clip") == 1
