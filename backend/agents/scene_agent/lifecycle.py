"""Checkpoint and row lifecycle retained by the scene agent."""

from typing import Any, Literal

from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from agents import loop
from agents.loop import Refused, ToolResult
from agents.models import ToolCall
from gateway.models import ModelCall
from gateway.types import (
    Image,
)
from jobs import (
    library,
    work,
)
from jobs.models import (
    QUALITY_FIELDS,
    AgentFix,
    AgentFixTry,
    Job,
    ProducedItem,
    Scene,
    SceneAgentRun,
    SceneStep,
)

from .media import _frames, judge_words, sent_list_words

AGENT = "scene_agent"
TURNS = "take_turn"


PAUSED_BETWEEN_TRIES = (
    "The shop owner paused the ad, so the fix waits between tries for the producer to carry it on."
)


CHECKED = {"line": ("line",), "shows": ("shows", "usage", "result")}


ROW = ("line", "shows", "usage", "result", "broll_kind", "person_shown", *QUALITY_FIELDS)
KEPT = (*ROW, "status", "fact_checked", "fact_problems", "shortened_from")


CANT_BE_VOICED = "the new line can't be voiced right now, so keep the scene's line as it is."


PLACE_NAMES = {
    "person_in_room": "picture of the presenter in its place",
    "room": "picture of its place on its own",
}


ENDED_BY_CAP = (
    "The scene agent filmed as many tries as it may at this scene, and none of them passed."
)


# The four cases an end picture fixes: guidance, never a limit (Julian, Nov 6 06:12/06:14).
END_PICTURE_CASES = (
    "An end picture (make_end_picture) is the clip's last frame, edited from its starting "
    "picture. It helps when the clip must end on a result where the product touched; when a "
    "push-in must end on a crop of the real label; when the clip drifts or ends in the wrong "
    "place; or when the action must stop at a set point, such as a press that stops part "
    "way down. These are examples, not a limit."
)


# The try-another-way ladder (Julian, Nov 9 2026, round 13 root cause 2): said whole in the
# instructions, and told again in two parts, each once a run.
LADDER_STEPS = (
    "1. A product part keeps coming out wrong: frame or angle the shot so that part is small "
    "or out of view. Never invent it.",
    "2. The action can't physically happen: film the nearest version that can, such as "
    "putting the product down, supporting it, or using the other hand.",
    "3. The movement fails twice: switch to a simpler action that still proves the line.",
    "4. Still failing: show the result instead of the process: the product in hand or beside "
    "the spot it touched, with the result visible and little motion.",
    "5. Still failing: switch the scene to a talking scene with change_scene. Keep the same "
    "line; pick a talking style and product state the plan's rules accept, a camera setup "
    "different from the talking scene before it and a product state different from it, so it "
    "fits the other scenes. A scene switched to talking this way gets 2 clips of its own, even "
    "when the run's six clips are used.",
    "6. If no talking version passes the plan's rules, or its clips fail too, give up: the "
    "scene keeps its last try in the ad and the owner can swap in any try.",
)

# Extra tries of a used-up picture budget a scene changed by the ladder gets.
CHANGED_SCENE_PICTURES = 3

# A B-roll scene never films a failed picture (Julian, Nov 9 2026 22:19-22:21).
NEVER_FILM_A_FAILED_PICTURE = (
    "A B-roll scene never films a failed starting or end picture: once its tries of a picture "
    f"are used up, change the scene with these steps; the changed scene gets "
    f"{CHANGED_SCENE_PICTURES} new tries of each picture used up. A talking scene may film its "
    "closest failed picture once its tries are used up."
)
LADDER_REFUSAL = "A B-roll scene never films a failed picture: change the scene with the ladder."

# Told once, at the run's first picture that failed its check.
PICTURE_LADDER = (
    "Fix a failed picture by what fails, and before you give up work down these steps:\n"
    + "\n".join(LADDER_STEPS[:2])
    + "\n"
    + NEVER_FILM_A_FAILED_PICTURE
)


# Told once, at the run's first clip the judge failed: where it goes wrong, then the rest of
# the ladder.
FIX_ORDER = (
    "Fix it by where the clip first goes wrong. Wrong in the first frame: draw a new starting "
    "picture. Goes wrong during the move: choose a simpler action. Ends wrong, or must stop "
    "at a set point: make an end picture. After the judge fails a clip, the same starting "
    "picture and end picture are not filmed again. " + END_PICTURE_CASES
)
CLIP_LADDER = (
    FIX_ORDER
    + "\nIf it keeps failing, work down the rest of the steps before you give up:\n"
    + "\n".join(LADDER_STEPS[2:])
)


MOST_TRIES = 6
# Tries for each kind of picture, counted separately within a run.
MOST_PICTURES = 6
# Clips a scene the ladder switched to talking gets of its own, past the run's six.
TALKING_SWITCH_CLIPS = 2


def budget_words(window: SceneAgentRun) -> str:
    """The run's tries used, added to every picture and film result."""
    return (
        f"Used in this run: starting pictures {window.starting_pictures} of "
        f"{window.starting_picture_limit}, end pictures {window.end_pictures} of "
        f"{window.end_picture_limit}, clips {window.clips} of {clip_limit(window)}."
    )


# Said when a B-roll scene has used its six clips: the run stays open for the ladder's step 5.
SIX_CLIPS_USED = (
    "This run's six clips are used. Switch the scene to a talking scene with change_scene "
    f"(step 5) for {TALKING_SWITCH_CLIPS} clips of its own, or give up."
)


def waits_for_the_talking_switch(window: SceneAgentRun, scene: Scene) -> bool:
    """Whether `window`'s B-roll scene has used its clips and may still take the ladder's
    talking step, so its run doesn't end by the cap."""
    return (
        scene.kind == Scene.Kind.BROLL
        and window.talking_switch_at is None
        and window.clips >= clip_limit(window)
    )


def clip_limit(window: SceneAgentRun) -> int:
    """Clips `window` may film: six, and a scene the ladder switched to talking gets
    TALKING_SWITCH_CLIPS of its own past the clips already used then."""
    if window.talking_switch_at is None:
        return MOST_TRIES
    return max(MOST_TRIES, window.talking_switch_at + TALKING_SWITCH_CLIPS)


def changed_by_the_ladder(window: SceneAgentRun, scene: Scene, *, was_broll: bool) -> None:
    """After change_scene changed `scene`'s action or kind: a used-up picture budget gets
    CHANGED_SCENE_PICTURES new tries, and a B-roll scene switched to talking its own clips."""
    window.refresh_from_db()
    if (
        window.starting_pictures >= window.starting_picture_limit
        or window.end_pictures >= window.end_picture_limit
    ):
        window.starting_picture_limit = max(
            window.starting_picture_limit, window.starting_pictures + CHANGED_SCENE_PICTURES
        )
        window.end_picture_limit = max(
            window.end_picture_limit, window.end_pictures + CHANGED_SCENE_PICTURES
        )
    if was_broll and scene.kind == Scene.Kind.TALKING:
        window.talking_switch_at = window.clips
    elif scene.kind == Scene.Kind.BROLL:
        window.talking_switch_at = None
    window.save(update_fields=["starting_picture_limit", "end_picture_limit", "talking_switch_at"])


def owner_asked(window: SceneAgentRun) -> bool:
    """Whether `window`'s run carries a change the shop owner asked for (the producer says so
    on pass_note): only then does the fix check gate a try. A producer's own re-plan isn't
    gated (round 13: Hypervolt's try 5 after the judge passed try 4)."""
    return any(quote.get("owner_change") for quote in window.quotes)


def picture_words(made: ProducedItem) -> str:
    """What a picture result adds: the picture steps of the ladder at the run's first picture
    that failed its check, and the run's tries used on every one."""
    pk = (made.sent or {}).get("run")
    window = SceneAgentRun.objects.filter(pk=pk).first() if pk is not None else None
    if window is None:
        return ""
    first_failed = (
        ProducedItem.objects.filter(
            scene=made.scene,
            kind__in=(ProducedItem.Kind.STARTING_PICTURE, ProducedItem.Kind.END_PICTURE),
            sent__run=window.pk,
            ruling__status="failed",
        )
        .order_by("created_at", "pk")
        .first()
    )
    ladder = (
        "\n" + PICTURE_LADDER if first_failed is not None and first_failed.pk == made.pk else ""
    )
    return ladder + "\n" + budget_words(window)


def _calls(fixing: AgentFix) -> QuerySet[ToolCall]:
    """Every tool the agent called, in order."""
    return fixing.tool_calls.exclude(tool=TURNS).order_by("created_at", "id")


def _turns(fixing: AgentFix) -> ToolCall:
    call, _ = ToolCall.objects.get_or_create(
        session=fixing.step.tool_call.session,
        call_id=f"{AGENT}-{fixing.pk}-turns",
        defaults={
            "job": fixing.step.scene.job,
            "agent": AGENT,
            "agent_fix": fixing,
            "tool": TURNS,
            "arguments": {},
            "finished_at": timezone.now(),
        },
    )
    return call


def _ended(fixing: AgentFix) -> bool:
    fixing.refresh_from_db()
    return fixing.finished_at is not None or fixing.passed_id is not None or bool(fixing.gave_up)


def _finish(fixing: AgentFix) -> None:
    """End the fixing: a passing try's row stays the scene's, carried by the try's own step
    (the step fixed keeps the row its first clip says); a give-up puts the scene back as it
    was before try 1."""
    if fixing.finished_at is not None:
        return
    with transaction.atomic():
        if fixing.passed_id is None:
            _put_back(fixing.step.scene, fixing.scene_before)
        fixing.finished_at = timezone.now()
        fixing.save(update_fields=["finished_at"])


def give_up(fixing: AgentFix, reason: str) -> None:
    """End the fixing as given up for `reason`: the scene is put back as it was before try 1."""
    fixing.gave_up = reason
    fixing.save(update_fields=["gave_up"])
    _finish(fixing)


def _parked_by_the_pause(fixing: AgentFix) -> bool:
    """Park the fix between tries while the shop owner's pause holds (spec #247 point 14a,
    Julian's Q21): it ends as a give-up does, its conversation kept and its last try in the
    ad, until the producer carries it on (`carry_on_after_the_pause`). Gives whether it
    is parked so."""
    if _ended(fixing):
        return fixing.paused_at is not None
    if not loop.paused(fixing.step.tool_call.session):
        return False
    with transaction.atomic():
        fixing.paused_at = timezone.now()
        fixing.save(update_fields=["paused_at"])
        give_up(fixing, PAUSED_BETWEEN_TRIES)
    return True


def _put_back(scene: Scene, row: dict[str, Any]) -> None:
    Scene.objects.filter(pk=scene.pk).update(**row)
    scene.refresh_from_db()


def _same_words(one: str, other: str) -> bool:
    return " ".join(one.split()).lower() == " ".join(other.split()).lower()


def _side(sent: dict[str, Any], side: str) -> tuple[str, ...]:
    """One side of a scene's text as a fact check was sent it: its fields' texts, in order."""
    return tuple(sent.get(field) or "" for field in CHECKED[side])


def _passed(job: Job, number: int) -> dict[str, set[tuple[str, ...]]]:
    """Each side of scene `number`'s text, as a fact check of `job` passed it whole and no
    later check failed it: the checks are read oldest first, an "ok" adding the scene's text
    as it was sent, a "wrong" taking away the side it blames."""
    passed: dict[str, set[tuple[str, ...]]] = {side: set() for side in CHECKED}
    for handoff, output in (
        ModelCall.objects.filter(job=job, purpose="fact_check", outcome=ModelCall.Outcome.SUCCEEDED)
        .order_by("created_at", "id")
        .values_list("handoff", "output")
    ):
        if not isinstance(output, dict) or output.get("decision") != "checked":
            continue
        verdicts = {line["scene"]: line for line in output.get("lines") or []}
        for sent in handoff.get("lines") or []:
            verdict = verdicts.get(sent["scene"]) if sent["scene"] == number else None
            if verdict is None:
                continue
            blamed = ("line", "shows") if verdict["wrong"] == "both" else (verdict["wrong"],)
            for side, texts in passed.items():
                if verdict["verdict"] == "ok":
                    texts.add(_side(sent, side))
                elif side in blamed:
                    texts.discard(_side(sent, side))
    return passed


def _try_step(fixing: AgentFix, call: ToolCall, kind: str, **made: Any) -> SceneStep:
    """A step of a try. It runs here, inside the scene step the agent fixes, never as a
    step of its own in the background: so it is never "running", which only that one is."""
    step = SceneStep.objects.create(
        scene=fixing.step.scene,
        kind=kind,
        status=SceneStep.Status.FINISHED,
        tool_call=call,
        **SceneStep.snapshot(fixing.step.scene),
        **made,
    )
    step.reference_photos.set(fixing.step.reference_photos.all())
    return step


def _failed(step: SceneStep, reason: str) -> None:
    step.status = SceneStep.Status.FAILED
    step.reason = reason[:500]
    step.finished_at = timezone.now()
    step.save(update_fields=["status", "reason", "finished_at"])


def _done(step: SceneStep) -> None:
    step.finished_at = timezone.now()
    step.save(update_fields=["finished_at"])


def _passes(row: AgentFixTry, clip: ProducedItem) -> bool:
    """Whether a try passes: neither its clip nor word check failed, and for door 2,
    its fix check did not fail. An unavailable check keeps the clip unchecked."""
    if (clip.ruling or {}).get("word_check", {}).get("verdict") == "fail":
        return False
    if (clip.ruling or {}).get("verdict") == "fail":
        return False
    return (row.fix_check or {}).get("verdict") != "failed"


def _told(
    fixing: AgentFix,
    row: AgentFixTry,
    *,
    changed: list[str] | None = None,
) -> ToolResult:
    """What the agent is told of a filmed try: what changed, what it was sent, its frames,
    both checks' words and, for door 2, whether the owner's change is made."""
    assert row.clip is not None
    label = f"Try {row.number}"
    pictures = [
        Image(label=each.label, key=each.key) for each in _frames(row.clip, f"{label}, new clip")
    ]
    words = [
        f"{label}.",
        *([f"Changed: {', '.join(changed) or 'only the prompts'}."] if changed is not None else []),
        *(
            [sent_list_words(library.SentList.model_validate(row.sent), whose=f"{label}'s clip")]
            if row.sent is not None
            else []
        ),
        judge_words(row.clip.ruling),
    ]
    checked_words = (row.clip.ruling or {}).get("word_check")
    if checked_words is not None:
        words.append(
            f"Word check: {checked_words['verdict']}. "
            f"The checked line: {checked_words['line']}\n"
            f"What was said: {checked_words['said'] or '(unavailable)'}\n"
            f"Why: {checked_words['reason']}"
        )
    if row.fix_check is not None:
        made = row.fix_check["verdict"]
        words.append(
            "Whether the owner's change is made: "
            + {"passed": "yes.", "failed": f"no. {row.fix_check['reason']}"}.get(
                made, "it couldn't be told."
            )
        )
    if not row.passed:
        if row.picture is not None:
            pictures.append(
                Image(label=f"{label}'s starting picture {row.picture.pk}", key=row.picture.file)
            )
    # The notes follow the clip judge's own fails, as the same-picture refusal does: a word
    # check's or fix check's fail leaves the picture allowed, so it gets no note.
    if (row.clip.ruling or {}).get("verdict") == "fail":
        fails = (
            AgentFixTry.objects.filter(
                agent_fix=fixing,
                sent__scene_run=(row.sent or {}).get("scene_run"),
                clip__ruling__verdict="fail",
            )
            .filter(Q(created_at__lt=row.created_at) | Q(created_at=row.created_at, pk__lte=row.pk))
            .count()
        )
        if fails == 1:
            words.append(CLIP_LADDER)
    run = (row.sent or {}).get("scene_run")
    window = SceneAgentRun.objects.filter(pk=run).first() if run is not None else None
    if window is not None:
        words.append(budget_words(window))
        scene = Scene.objects.get(pk=fixing.step.scene_id)
        if not row.passed and waits_for_the_talking_switch(window, scene):
            words.append(SIX_CLIPS_USED)
    words.append("It passes." if row.passed else "It fails.")
    return ToolResult("\n".join(words), pictures)


def _refuse_a_missing_own_place(scene: Scene, person_shown: str) -> None:
    """Refuse a try whose face needs the picture of the scene's own place that place_edit
    hasn't made, so it would go in the ad's old place (#237)."""
    newest = work.newest_own_place(scene)
    if newest is None:
        return
    drawn = work.drawn_for(person_shown)
    if work.own_place(scene, drawn) is None:
        raise Refused(
            f"the scene's newest place is its own {PLACE_NAMES[_drawn_as(newest)]}, and this "
            f"try needs its {PLACE_NAMES[drawn]}; "
            "give_up so the producer can prepare that place first."
        )


def _drawn_as(own: ProducedItem) -> Literal["person_in_room", "room"]:
    """Which picture of a scene's place `own` is."""
    return "person_in_room" if own.kind == ProducedItem.Kind.SCENE_PERSON_IN_ROOM else "room"


def _validate_line(line: str) -> None:
    """Refuse live speech outside the fixed limits, before checking or filming it."""
    from jobs.pace import (
        FIXED_WORDS_PER_SECOND,
        SHORTEST_BROLL_LINE_SECONDS,
        fewest_words_in_a_broll_line,
        h3_clip_seconds,
    )

    words = len(line.split())
    minimum = fewest_words_in_a_broll_line(FIXED_WORDS_PER_SECOND)
    if words < minimum:
        raise Refused(
            f"The line needs at least {minimum} words ({SHORTEST_BROLL_LINE_SECONDS} seconds of "
            "speech) "
            "before it can be filmed."
        )
    try:
        h3_clip_seconds(words)
    except ValueError as error:
        raise Refused(str(error)) from error


def _row(scene: Scene) -> dict[str, Any]:
    return {field: getattr(scene, field) for field in ROW}
