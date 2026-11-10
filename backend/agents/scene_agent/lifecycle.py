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


# The try-another-way ladder (round 13 root cause 2, spec #514): said whole in the scene
# agent's instructions, and its picture and clip steps told again, each once a run.
LADDER_STEPS = (
    "1. From the first failed picture, starting or end, make a bigger change than a small "
    "edit. A wrong part the scene doesn't need (the line doesn't name it and the action "
    "doesn't use it): hide it right away: angle it away, frame it out, or cover it with a "
    "hand, but only with a natural grip on that spot. Never invent it, and don't spot-edit it. "
    "A wrong part the scene needs (the line names it or the action uses it): never hide it. "
    "Small print or marks: draw the product closer to the camera so that part is big and "
    "clear. A wrong shape or assembly: a fresh drawing, not an edit.",
    "2. The action can't physically happen: film the nearest version that can, such as "
    "putting the product down, supporting it, or using the other hand.",
    "3. The movement fails twice: switch to a simpler action that still proves the line.",
    "4. Still failing: show the result instead of the process: the product in hand or beside "
    "the spot it touched, with the result visible and little motion.",
    "5. Still failing: switch the scene to a talking scene with change_scene. Keep the same "
    "line; pick a talking style and product state the plan's rules accept, with a camera "
    "setup and a product state different from the talking scene before it, so it fits the "
    "other scenes.",
    "6. If no talking version passes the plan's rules, or its clips fail too, give up: the "
    "scene keeps its last try in the ad and the owner can swap in any try.",
)


# Told once, at the run's first starting or end picture that failed its check.
PICTURE_STEPS = (
    "A picture failed its check. Fix it by what fails, and before you give up, work down "
    "these steps:\n" + "\n".join(LADDER_STEPS[:2])
)


# Told once a run, at its first clip the judge failed: where the clip goes wrong, then the
# ladder's clip steps.
FIX_ORDER = (
    "Fix it by where the clip first goes wrong. Wrong in the first frame: draw a new starting "
    "picture. Goes wrong during the move: choose a simpler action. Ends wrong, or must stop "
    "at a set point: make an end picture. After the judge fails a clip, the same starting "
    "picture and end picture are not filmed again. " + END_PICTURE_CASES
)

CLIP_STEPS = (
    FIX_ORDER
    + "\nIf it keeps failing, work down these steps before you give up:\n"
    + "\n".join(LADDER_STEPS[2:5])
)


MOST_TRIES = 6
# Tries for each kind of picture, counted separately within a run.
MOST_PICTURES = 6


def tries_used(run: int | None) -> str:
    """The line every picture and film result ends on: the tries used of each limit by the
    scene agent's current run, which is `run` unless a note opened a newer one while `run`'s
    work was in flight."""
    made_in = SceneAgentRun.objects.filter(pk=run).first() if run is not None else None
    if made_in is None:
        return ""
    window = made_in.agent_fix.scene_runs.latest("created_at", "pk")
    return (
        f"\nUsed in this run: starting pictures {window.starting_pictures} of {MOST_PICTURES}, "
        f"end pictures {window.end_pictures} of {MOST_PICTURES}, "
        f"clips {window.clips} of {MOST_TRIES}."
    )


def picture_words(made: ProducedItem) -> str:
    """What code adds to a starting or end picture's result: the ladder's picture steps when
    `made` is its run's first picture that failed its check, and the tries the run has used."""
    run = (made.sent or {}).get("run")
    return _picture_steps(made, run) + tries_used(run)


def _picture_steps(made: ProducedItem, run: int | None) -> str:
    if run is None or made.scene_id is None:
        return ""
    first_failed = (
        ProducedItem.objects.filter(
            scene_id=made.scene_id,
            kind__in=(ProducedItem.Kind.STARTING_PICTURE, ProducedItem.Kind.END_PICTURE),
            sent__run=run,
            ruling__status="failed",
        )
        .order_by("created_at", "pk")
        .first()
    )
    if first_failed is None or first_failed.pk != made.pk:
        return ""
    return "\n" + PICTURE_STEPS


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


def owner_asked(window: SceneAgentRun) -> bool:
    """Whether the run's change came from the shop owner: the producer marked its note so.
    Only such a run's fix check can fail a try."""
    return any(quote.get("owner_asked") for quote in window.quotes)


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
            words.append(CLIP_STEPS)
    words.append("It passes." if row.passed else "It fails.")
    return ToolResult("\n".join(words) + tries_used((row.sent or {}).get("scene_run")), pictures)


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
