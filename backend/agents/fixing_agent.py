"""The fixing agent: one agent owns a failed B-roll scene until it is fixed or honestly given
up (handoffs/fixing-agent/spec.md). Bad means the clip judge failed the scene's clip (door 1)
or the shop owner sent a note about it (door 2). The agent sees the plan, the page, the shop
photos, the place, the failed clip's frames and what the judge or the owner said; it decides
what to change (the action, the result, the kind, the face, the framing, the start picture,
both prompts, the line), films again with the app's own steps, and reads the judge's answer.
It repeats until a try passes, or it calls give_up, or it has filmed the tries cap, when
code ends it; it is never told the cap. As its last resort at door 1 it may ask the shop
owner once (ask_owner): the fix is then parked on that question, ending as a give-up does
until `answer_question` gives the answer and opens it again. Every try is a version the shop
owner can see and pick.

Nothing is kept in memory between turns: each turn is rebuilt from the AgentFix row and the
checkpoint of every tool the agent called, so a worker that stops halfway is replaced by one
that carries on where the checkpoints say, paying for nothing twice."""

import io
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, ClassVar, Literal

import PIL.Image
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from adforge import file_store, tracing
from gateway.gateway import charged_to, edit_picture
from gateway.types import (
    MOST_BROLL_SECONDS,
    BrollClipHandoff,
    Happened,
    Image,
    Said,
    Shown,
    ToolSpec,
    ToolUse,
)
from jobs import (
    auto_fix,
    clip_judge,
    cut_and_send,
    library,
    library_items,
    picture_check,
    product_videos,
    reference_photos,
    scenes,
    work,
)
from jobs.first_send import PIECE_LABEL, SPEED_LINE, drawn_label
from jobs.library import Moment
from jobs.models import (
    AgentFix,
    AgentFixTry,
    Job,
    OwnerQuestion,
    ProducedItem,
    ProductVideo,
    ReferencePhoto,
    Scene,
    SceneStep,
)
from jobs.planning import ScenePicture, ScenePiece

from . import loop
from .loop import Refused, Tool, ToolResult
from .models import ToolCall

AGENT = "fixing_agent"
PURPOSE = "fix_scene"

# The most tries the agent may film for one scene: a guard on money and time, never a
# decision about the scene (Julian 2026-10-19 16:45, "We cannot make that decision with the
# code"). The agent decides when to give up.
MOST_TRIES = 6
# A start picture that fails its check is drawn again, as the app does, at most this often
# before the try ends with no clip.
MOST_REDRAWS = picture_check.MOST_REDRAWS
# The newest pictures the agent is shown on a turn; older ones are dropped with a note.
MOST_PICTURES = 50

# The scene's fields the agent may change, as a film_again writes them.
ROW = ("line", "shows", "result", "broll_kind", "person_shown")
# What is put back on a give-up: the row and what follows from it.
KEPT = (*ROW, "status", "fact_checked", "fact_problems", "shortened_from")

# Julian's one rule from the dropped API-limits list (spec #210 point 22; Oct 28 23:31, audit
# thread): when words don't fix a clip, a reference does.
WORDS_THEN_REFERENCES = (
    "If fixing it with words doesn't fix it, use the reference video or photos: photos for how "
    "it looks, the video for the movement."
)

INSTRUCTIONS = f"""\
You fix one B-roll scene of a short vertical video ad. In a B-roll scene the presenter's \
voice says the scene's line while the clip shows the product. The scene's clip failed: a \
judge watched it and found faults, or the shop owner failed it. You own this scene. You may \
change, for this scene only: what it shows (the action), what it ends on (the result), its \
B-roll kind, whether the presenter's face is in it, the camera framing, the start picture \
(you write its prompt from scratch), the video prompt (from scratch), and the line. You may \
film a different step of what the line says, when that step films better. The clip lasts the \
line's audio in whole seconds, at least 5 and at most 15, so a shorter or longer line changes \
the clip's length. You never change another scene. Every B-roll scene happens in the ad's \
place; you may change this scene's place with place_edit, if you choose: a small edit of its \
picture of the place, which is cheaper and keeps the look already approved, or a new place \
drawn fresh.

What you know about the video model, from 37 clips this app made:
- It follows orders that say what happens. It ignores orders that say what must not happen: \
a video prompt listing bans made every remake fail. Write every order as what happens, what \
stays where it is, and how it ends.
- An action that is over in a moment, stretched over a clip of 5 seconds or more, fails: the \
model fills the spare seconds with things that never happen in life. Every clip that passed \
had an action that keeps going by its own course for the whole clip. So pick an action that \
fills the clip, or write the clip second by second so each second has its real part: the \
reach, the action, and what it naturally causes as it settles, ending on the result.
- The clip is made from your start picture as a looks guide: its first frame is a redraw of \
it. So the start picture must show the moment just before the action, with everything the \
action needs already in place and held the way a real person holds it, and nothing in it \
that the action contradicts.

Rules you keep, always:
- Positive orders only, in the video prompt: no "not", "no", "never", "without", "nothing", \
"nobody", "none", "neither", "nor", "avoid", or any word ending in "n't". Code refuses a \
video prompt with them.
- ONE action per clip. The camera never moves. Only "real-time speed": never slower. The \
start picture is an order. Never turn the product to a side the photos don't show. The \
result shows only where the product touches. In a scene that shows the problem, nobody \
touches the product.
- The line stays a true claim from the page; a new line, or a new action, is fact checked \
against the page, and code refuses one that fails. What the page says about how the product \
is used comes first; where the page says nothing about it, go by common sense about how such \
a product is really used.
- When the shop owner sent a note, do what it asks; where the note names movements to film, \
film every one in its order (only there may a clip have more than one action).
{{asking}}

How you work: you are shown the scene, the plan, the page, what the failed clip was sent \
(or its start picture), the failed clip's frames (5 a second), what the judge found, the \
ad's picture library as a list and how the product behaves. Look at anything else you need: \
look_at_library opens one library item, or one moment of a page video, by its number. Then \
make a move, whichever fixes the cause, in no set order: film_again films the whole scene as \
you write it, and its references send library items by number, in your order, and a page \
video's moment as the piece, in place of the start picture (code sends them after the \
front photo and before the scene's place and the other sides, makes them the clip's \
colour, labels each one and puts the speed line before your prompt); change_action \
films another selling action, with its line, that you write; \
end_result_only films only the end state the action leads to; place_edit shows you the \
scene's new place before anything is filmed; give_up ends the fix. After each filmed move \
you are told what was sent, shown the new start picture when you drew one and the new \
clip's frames, and given the judge's verdict. When code \
refuses a move, its answer says why: that costs no try, so change what it names and call \
again. On a fail, find what caused it (the plan, the pictures, or what the video model can't \
do) and change that cause, not only the words. {WORDS_THEN_REFERENCES} On every try, write \
the cause you found and your move. Try to film it first: code refuses give_up until at least \
one new clip was made. When, after trying, you judge the scene can't be filmed well, call \
give_up with your reason: {{gave_up}} The judge can be wrong, but only a clip the judge \
passes passes.

The app's own prompt rules follow, for writing the two prompts. They hold for you too, with \
two exceptions: you may write the video prompt second by second, with timings, when that \
helps; and a rule about following a producer's note does not apply.
"""


# Whether the agent may ask the shop owner, as it is told, by door (spec #210 point 23; Julian's
# design, 2026-10-29): door 1's scene keeps its last try while the question waits, so it may
# ask, as its last resort; door 2's fix answers the owner's own note, so it never asks.
ASKING = {
    AgentFix.Door.JUDGE: "- Ask the shop owner only as your last resort, when the ad's library "
    "holds nothing for what the fix needs: call ask_owner with your own question, for a video, "
    "a photo or words. You may ask once in this scene's fix. The fix then waits for the answer, "
    "the last try staying in the ad; the answer comes back as ask_owner's answer, with the new "
    "library item it made. Anything else you would ask them, write in would_ask_owner and carry "
    "on as best you can.",
    AgentFix.Door.OWNER: "- Never ask the shop owner anything. If you would need them (for "
    "example to know how the product is really used), write it in would_ask_owner and carry on "
    "as best you can.",
}


# What giving up does, as the agent is told it, by door: door 1's scene keeps its last try
# (Julian, 2026-10-23: "remove that rule - switching to talking scene"); door 2's keeps its
# clip as it was (spec section 2; Julian's OK 2026-10-19 20:10).
GAVE_UP = {
    AgentFix.Door.JUDGE: "the last try stays in the ad, and the shop owner is shown every try "
    "and your reason.",
    AgentFix.Door.OWNER: "the scene keeps its clip as it was, and the shop owner is shown every "
    "try and your reason.",
}
# The give_up tool's description for door 2; door 1's is the tool's own.
GIVE_UP_FOR_A_NOTE = (
    "Give up on filming this scene: it keeps its clip as it was, and the shop owner is shown "
    "every try and your reason. Say why, and which try you think is best."
)
# What give_up answers, by door.
GIVEN_UP = {
    AgentFix.Door.JUDGE: "The scene is given up: its last try stays in the ad, and the shop "
    "owner is shown every try and your reason.",
    AgentFix.Door.OWNER: "The scene is given up: it keeps its clip as it was, and the shop "
    "owner is shown every try and your reason.",
}


def _gives_up_as(fixing: AgentFix) -> AgentFix.Door:
    """Which door's giving up `fixing` is told of: door 1's on a step of its own, keeping its
    last try; else door 2's, as a fix's step keeps its clip from before whichever door took
    it."""
    if fixing.door == AgentFix.Door.JUDGE and work.keeps_the_last_try_on_give_up(fixing.step):
        return AgentFix.Door.JUDGE
    return AgentFix.Door.OWNER


def instructions(door: str = AgentFix.Door.JUDGE) -> str:
    """Everything the agent is told for `door`: its own instructions, saying what giving up
    does there, then the app's B-roll prompt rules, unchanged."""
    kinds = "\n\n".join(
        f'When the B-roll kind is "{kind}":\n{rules}'
        for kind, rules in scenes.BROLL_KIND_RULES.items()
    )
    told = INSTRUCTIONS.replace("{gave_up}", GAVE_UP[AgentFix.Door(door)]).replace(
        "{asking}", ASKING[AgentFix.Door(door)]
    )
    return f"{told}\n{scenes.BROLL_SHARED_RULES}\n\n{kinds}"


def _tools(door: str) -> list[ToolSpec]:
    """The agent's tools, give_up described as giving up does at `door`."""
    specs = [tool.spec() for tool in TOOLS]
    if door != AgentFix.Door.OWNER:
        return specs
    return [
        replace(spec, description=GIVE_UP_FOR_A_NOTE) if spec.name == GiveUp.name else spec
        for spec in specs
        if spec.name != AskOwner.name
    ]


# What a fixing the tries cap ended says, as its give-up reason, for the shop owner and the
# producer: code ended it, never the agent.
ENDED_BY_CAP = (
    "The fixing agent filmed as many tries as it may at this scene, and none of them passed."
)


# What a call is told once the fixing has ended, by a pass, a give-up or the tries cap,
# never naming a count.
ENDED = "the scene's fixing has ended."

# What a parked fix says, as its give-up reason, for the shop owner and the producer: the
# agent asked the shop owner a question (spec #210 point 23), and the fix waits for the
# answer, the scene keeping its last try meanwhile.
ASKED_THE_OWNER = (
    "The fixing agent asked the shop owner a question about this scene, and the fix waits for "
    "the answer."
)
# What a call after the ask, in the turn that asked, is told: nothing is done while the fix
# waits.
WAITS = "the fix waits for the shop owner's answer to your question."


# What a move that would leave the scene showing nothing is told: a talking scene is never
# the agent's (spec 2 item 38).
NO_TALKING = (
    "the scene must show an action or its result: a talking scene is never filmed by you, "
    "so write what the clip shows."
)


# What a video prompt may not say: the video model ignores bans (A2).
BANNED = re.compile(
    r"\b(not|no|never|without|nothing|nobody|none|neither|nor|avoid|avoids|avoiding)\b|n't\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------- the interface


@dataclass(frozen=True)
class Failure:
    """What failed, handed to the agent. Door 1: the judge's ruling of try 0. Door 2: the
    owner's note, with the judge's ruling of the clip they saw given for reference. `clip`
    is try 0, the clip that failed; by default the step's own clip (door 1), or the clip the
    owner saw last before the step started (door 2)."""

    ruling: dict[str, Any] | None = None
    note: str = ""
    clip: ProducedItem | None = None


@dataclass(frozen=True)
class Try:
    """One try, as filmed: a version the shop owner can see and pick."""

    number: int
    clip: ProducedItem | None
    picture: ProducedItem | None
    scene_row: dict[str, Any]
    picture_prompt: str
    video_prompt: str
    ruling: dict[str, Any] | None
    fix_check: dict[str, Any] | None
    why: str
    would_ask_owner: str
    cost_usd: Decimal
    # The agent's pick of its tries, on a give-up.
    picked: bool
    # The move the try was filmed by: the tool the agent called (film_again, change_action,
    # end_result_only).
    tool: str = "film_again"


@dataclass(frozen=True)
class Outcome:
    """How the agent's work on a scene ended: the passing try's clip, or why it gave up."""

    passed: ProducedItem | None
    tries: list[Try]
    gave_up: str | None
    cost_usd: Decimal
    # Whether code ended the fix at the tries cap, rather than the agent giving up.
    ended_by_cap: bool = False
    # Whether the fix is parked on a question for the shop owner (`work.open_question`).
    parked: bool = False


def fix_scene(
    step: SceneStep,
    *,
    door: Literal["judge", "owner"],
    failure: Failure,
    goal: str | None,
    cap: int | None = None,
) -> Outcome:
    """Have the agent fix `step`'s scene until a try passes or it gives up. A passing try's
    scene row is the scene's from then on, on its record and `step`'s; a give-up puts the
    scene back as it was before try 1, and so does an error that stops the agent, which is
    raised once it has. Run again for the same step, as after a worker stopped, it carries on
    from its checkpoints, or gives the outcome it ended on. `cap` is the most tries it may
    film, MOST_TRIES unless given; the agent is never told it."""
    fixing = _started(step, door, failure, goal, MOST_TRIES if cap is None else cap)
    if fixing.finished_at is None:
        try:
            _work(fixing)
        except Exception:
            _stopped(fixing)
            raise
    return outcome(fixing)


def outcome(fixing: AgentFix) -> Outcome:
    """How `fixing` ended, or stands."""
    tries = [_as_try(each, fixing) for each in fixing.tries.select_related("clip", "picture")]
    return Outcome(
        passed=fixing.passed,
        tries=tries,
        gave_up=fixing.gave_up or None,
        cost_usd=sum((call.cost_usd() for call in fixing.tool_calls.all()), Decimal(0)),
        ended_by_cap=fixing.ended_by_cap,
        parked=work.is_parked(fixing),
    )


def _as_try(row: AgentFixTry, fixing: AgentFix) -> Try:
    return Try(
        number=row.number,
        clip=row.clip,
        picture=row.picture,
        scene_row=row.scene_row,
        picture_prompt=row.picture_prompt,
        video_prompt=row.video_prompt,
        ruling=row.clip.ruling if row.clip is not None else None,
        fix_check=row.fix_check,
        # A try filmed since #229 writes its root cause and move in place of a why.
        why=row.why or f"{row.root_cause} {row.move}".strip(),
        would_ask_owner=row.would_ask_owner,
        cost_usd=row.tool_call.cost_usd(),
        picked=fixing.picked == row.number,
        tool=row.tool_call.tool,
    )


def _started(step: SceneStep, door: str, failure: Failure, goal: str | None, cap: int) -> AgentFix:
    """The step's AgentFix: started now with the scene as it stands, or as it was started."""
    with transaction.atomic():
        found = AgentFix.objects.select_for_update().filter(step=step).first()
        if found is not None:
            return found
        # As it stands now: the step's own copy may be from before its clip was made.
        scene = Scene.objects.get(pk=step.scene_id)
        if not scene.shows:
            # The agent films B-roll; a talking scene is never its (spec 2 item 38).
            raise ValueError(f"scene {scene.number} is a talking scene: the agent never fixes one")
        first = failure.clip or (
            step.produced.filter(kind=ProducedItem.Kind.CLIP).order_by("id").first()
            if door == AgentFix.Door.JUDGE
            else step.shown_before(ProducedItem.Kind.CLIP)
        )
        assert first is not None, "the agent fixes a scene that has a clip"
        return AgentFix.objects.create(
            step=step,
            door=door,
            note=failure.note,
            goal=goal or "",
            first_clip=first,
            ruling=failure.ruling if failure.ruling is not None else first.ruling,
            scene_before={field: getattr(scene, field) for field in KEPT},
            cap=cap,
        )


# ---------------------------------------------------------------- the agent's loop


def _work(fixing: AgentFix) -> None:
    # A tool that was running when the last worker stopped runs again first.
    for call in _calls(fixing).filter(finished_at__isnull=True):
        _settle_unless_ended(fixing, call)
    # A worker that stopped after the last try was filmed ends the fixing as it would have.
    _end_at_the_cap(fixing)
    # No limit on the agent's turns (spec #210, point 24): only the tries cap ends it.
    while not _ended(fixing):
        with tracing.agent_turn(AGENT), charged_to(_turns(fixing)):
            turn = loop.take_a_turn(
                session=fixing.step.tool_call.session,
                job=fixing.step.scene.job,
                purpose=PURPOSE,
                instructions=instructions(_gives_up_as(fixing)),
                conversation=_fit(_conversation(fixing)),
                tools=_tools(_gives_up_as(fixing)),
                since=fixing.created_at,
                done=f"the fixing of scene {fixing.step.scene.number} carried on",
            )
        with transaction.atomic():
            at = _calls(fixing).count()
            if turn.says or not turn.calls:
                fixing.said = [
                    *fixing.said,
                    {"at": at, "text": turn.says, "no_call": not turn.calls},
                ]
                fixing.save(update_fields=["said"])
            calls = [
                ToolCall.objects.create(
                    session=fixing.step.tool_call.session,
                    job=fixing.step.scene.job,
                    agent=AGENT,
                    agent_fix=fixing,
                    tool=asked.tool,
                    call_id=asked.call_id,
                    arguments=asked.arguments,
                )
                for asked in turn.calls
            ]
        for call in calls:
            _settle_unless_ended(fixing, call)
    _finish(fixing)


# The checkpoint the agent's own turns are charged to, so every model call of a fixing is
# charged to one of its tool calls. It is no tool the agent called.
TURNS = "take_turn"


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


def _calls(fixing: AgentFix) -> QuerySet[ToolCall]:
    """Every tool the agent called, in order."""
    return fixing.tool_calls.exclude(tool=TURNS).order_by("created_at", "id")


def _settle_unless_ended(fixing: AgentFix, call: ToolCall) -> None:
    """Settle the call, or, once the fixing has ended or is parked, refuse it with nothing
    done."""
    if _ended(fixing):
        call.result = f"Refused: {WAITS if work.is_parked(fixing) else ENDED} Nothing was done."
        call.finished_at = timezone.now()
        call.save(update_fields=["result", "finished_at"])
    else:
        _settle(fixing, call)


def _settle(fixing: AgentFix, call: ToolCall) -> None:
    """Run the call, as every agent's. The agent is never told how many tries it has or has
    left, so that it never quits early (spec #210, point 29). An ask the agent may make parks
    the fix, its call left awaiting the owner's answer."""
    if call.tool == AskOwner.name and _may_ask(fixing, call):
        _park(fixing, call)
    else:
        loop.settle(AGENT_SPEC, call, counted=False)
    fixing.refresh_from_db()
    _end_at_the_cap(fixing)


def _end_at_the_cap(fixing: AgentFix) -> None:
    """End the fixing, by code, once the agent has filmed its cap of tries and none passed:
    a give-up whose record says the cap ended it. The agent is never asked to give up."""
    if _ended(fixing) or fixing.tries.count() < fixing.cap:
        return
    with transaction.atomic():
        fixing.ended_by_cap = True
        fixing.save(update_fields=["ended_by_cap"])
        give_up(fixing, ENDED_BY_CAP)


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


def _stopped(fixing: AgentFix) -> None:
    """End a fixing an error stopped: the scene is put back as it was before try 1, so no try
    the judge failed is its, and the fixing ends with no try passed and no give-up."""
    with transaction.atomic():
        fixing = AgentFix.objects.select_for_update().get(pk=fixing.pk)
        if fixing.finished_at is not None:
            return
        _put_back(fixing.step.scene, fixing.scene_before)
        fixing.passed = None
        fixing.finished_at = timezone.now()
        fixing.save(update_fields=["passed", "finished_at"])


def give_up(fixing: AgentFix, reason: str) -> None:
    """End the fixing as given up for `reason`: the scene is put back as it was before try 1."""
    fixing.gave_up = reason
    fixing.save(update_fields=["gave_up"])
    _finish(fixing)


def _may_ask(fixing: AgentFix, call: ToolCall) -> bool:
    """Whether the agent's ask_owner `call` parks the fix: its arguments are right and
    nothing stops it asking. Else the tool refuses it, saying why."""
    try:
        AskOwner.model_validate(call.arguments)
    except ValueError:
        return False
    return _why_it_may_not_ask(fixing) is None


def _why_it_may_not_ask(fixing: AgentFix) -> str | None:
    if _gives_up_as(fixing) != AgentFix.Door.JUDGE:
        return "the shop owner can't be asked in this fix."
    if OwnerQuestion.objects.filter(agent_fix=fixing).exists():
        return (
            "you asked the shop owner once in this scene's fix already, and that is all: carry "
            "on with what you have."
        )
    return None


def _park(fixing: AgentFix, call: ToolCall) -> None:
    """Park the fix on the agent's question (Julian's design, 2026-10-29): the question is
    stored on the scene, open; the fix ends as a give-up does, so the scene keeps its last try
    and the ad finishes; the ask_owner call stays the conversation's last, awaiting its result,
    which `answer_question` gives. Nothing films on its own."""
    asked = AskOwner.model_validate(call.arguments)
    with tracing.tool(call.tool, call.arguments) as traced:
        call.trace_id, call.observation_id = traced.ids()
        call.save(update_fields=["trace_id", "observation_id"])
        with transaction.atomic():
            OwnerQuestion.objects.create(
                scene=fixing.step.scene,
                agent_fix=fixing,
                tool_call=call,
                question=asked.question,
                wants=asked.wants,
            )
            give_up(fixing, ASKED_THE_OWNER)
        tracing.handed_back(traced, ASKED_THE_OWNER)


def answer_question(question: OwnerQuestion, answer: ToolResult) -> AgentFix:
    """The resume entry point for a parked fix (#245): the owner's `answer` (its words and
    the new library item's picture) becomes the result of the agent's ask_owner call, the
    question is answered, and the fix is open again, as before it was parked. The next
    `fix_scene` run for its step then carries on from the checkpoints, the agent reading the
    answer as ask_owner's. Gives the fix."""
    with transaction.atomic():
        call = ToolCall.objects.select_for_update().get(pk=question.tool_call_id)
        assert call.finished_at is None, "a question is answered once"
        call.result = answer.text
        call.pictures = [{"label": each.label, "key": each.key} for each in answer.pictures]
        call.finished_at = timezone.now()
        call.save(update_fields=["result", "pictures", "finished_at"])
        question.answered_at = call.finished_at
        question.save(update_fields=["answered_at"])
        fixing = AgentFix.objects.select_for_update().get(pk=question.agent_fix_id)
        fixing.gave_up = ""
        fixing.picked = None
        fixing.kept = None
        fixing.finished_at = None
        fixing.save(update_fields=["gave_up", "picked", "kept", "finished_at"])
    return fixing


def _put_back(scene: Scene, row: dict[str, Any]) -> None:
    Scene.objects.filter(pk=scene.pk).update(**row)
    scene.refresh_from_db()


def _fit(conversation: list[Happened]) -> list[Happened]:
    """Keep the newest pictures under MOST_PICTURES; older ones are dropped with a note. What
    the agent opened in the library stays for the whole fix (spec #210 point 22), each look
    shown once, on its newest copy, within LIBRARY_ROOM (look_at_library refuses a look past
    it), so the newest try's frames always have their room and a turn never holds more than
    MOST_PICTURES."""
    library_room = _library_room()
    room = MOST_PICTURES
    seen: set[tuple[str, ...]] = set()
    kept: list[Happened] = []
    for each in reversed(conversation):
        if isinstance(each, ToolUse) and each.pictures and each.tool == LookAtLibrary.name:
            keys = tuple(picture.key for picture in each.pictures)
            if keys in seen:
                each = each.model_copy(
                    update={"pictures": [], "result": f"{each.result} {SHOWN_LATER}"}
                )
            elif len(keys) <= library_room:
                seen.add(keys)
                library_room -= len(keys)
                room -= len(keys)
            else:
                # Only when the room was made smaller since the look: never past MOST_PICTURES.
                each = each.model_copy(
                    update={"pictures": [], "result": f"{each.result} {NOT_SHOWN}"}
                )
        kept.append(each)
    fitted: list[Happened] = []
    for each in kept:
        if isinstance(each, ToolUse) and each.pictures and each.tool != LookAtLibrary.name:
            if len(each.pictures) <= room:
                room -= len(each.pictures)
            else:
                each = each.model_copy(
                    update={"pictures": [], "result": f"{each.result} {NOT_SHOWN}"}
                )
        fitted.append(each)
    return list(reversed(fitted))


# The share of a turn's MOST_PICTURES kept for what the agent opened in the library, the rest
# for the tries' pictures (not approved yet: one setting).
LIBRARY_SHARE = 0.5


def _library_room() -> int:
    """How many of a turn's pictures may be library pictures the agent opened."""
    return int(MOST_PICTURES * LIBRARY_SHARE)


NOT_SHOWN = "(Its pictures aren't shown any more: look again to see them.)"
SHOWN_LATER = "(Its pictures are shown where you opened it again, later.)"
LIBRARY_FULL = (
    "the library pictures you have open fill the room kept for them, so nothing more can be "
    "opened in this scene's fix: work from what you have open, or open one of those again."
)


REMINDER = "Answer with a tool call: a look, a move or give_up."

# What the first message says of a failed clip made before clips were sent references.
NO_SENT_LIST = (
    "The failed clip was made from a start picture, before the app sent clips references, so "
    "it has no sent list: what it was sent is its start picture, shown with it as try 0."
)
# The drawn picture of a sent list, by what it shows (jobs.library.Drawn).
DRAWN = {
    "person_in_room": "the drawn picture of the presenter in the ad's room",
    "room": "the drawn picture of the ad's room",
}


def _conversation(fixing: AgentFix) -> list[Happened]:
    """What the agent is given: the scene and what failed, try 0 shown, then each turn's
    words and tools in the order they happened."""
    first = fixing.first_clip
    assert first is not None, "the fixing started with try 0"
    sent, shown = try_shown("Try 0", first.picture, first)
    happened: list[Happened] = [
        Said(by="user", text=_intro(fixing)),
        ToolUse(
            call_id="try-0",
            tool=LookAtTry.name,
            arguments={"number": 0},
            result="Try 0: the scene as it failed in the ad. "
            + (
                f"What its clip was sent, then the clip's frames. {sent}"
                if sent
                else "Its start picture, then its clip's frames."
            ),
            pictures=[Shown(label=each.label, key=each.key) for each in shown],
        ),
    ]
    said = list(fixing.said)
    for number, call in enumerate(_calls(fixing).filter(finished_at__isnull=False)):
        happened += _said(said, number)
        happened.append(
            ToolUse(
                call_id=call.call_id,
                tool=call.tool,
                arguments=call.arguments,
                result=call.result,
                pictures=[Shown(**picture) for picture in call.pictures],
            )
        )
    happened += _said(said, None)
    return happened


def _said(said: list[dict[str, Any]], at: int | None) -> list[Happened]:
    """What the agent said before its tool call number `at` (None: after its last), with a
    reminder after a turn that called no tool."""
    happened: list[Happened] = []
    for each in [each for each in said if at is None or each["at"] == at]:
        said.remove(each)
        happened.append(Said(by="agent", text=each["text"] or "(nothing)"))
        if each["no_call"]:
            happened.append(Said(by="user", text=REMINDER))
    return happened


def _intro(fixing: AgentFix) -> str:
    """The first message: the failure (the judge's fixed-form fault, or the owner's note),
    what the failed clip was sent, the library as a text list with the page videos' moments,
    and the planner's note on how the product behaves (spec #210 point 22); then the scene,
    the plan and the page. It never names a count of tries."""
    step = fixing.step
    scene = step.scene
    job = scene.job
    first = fixing.first_clip
    assert first is not None and first.made_from is not None
    plan = "\n".join(_scene_facts(each) for each in job.scenes.order_by("number"))
    colour = [p.position for p in job.photos.filter(shows_product_colour=True)]
    every = [p.position for p in job.photos.all()]
    seconds = first.made_from.seconds or 0
    clip_seconds = work._broll_clip_seconds(seconds)
    during = [row.thing for row in _owner_photos(step, ReferencePhoto.Use.CLIP)]
    opener = (
        f"{reference_photos.reference_mode_label(during)} "
        f"{scenes.broll_video_prompt('', clip_seconds).rstrip()}"
    )
    sent = library.sent_list_of(first)
    if fixing.door == AgentFix.Door.OWNER:
        failed = (
            f"The shop owner watched this clip in their ad and sent this note about scene "
            f'{scene.number}: "{fixing.note}"\nThe note is what must change. What must be '
            f"different, in the owner's words: {fixing.goal}\nWhat the judge said about this "
            f"same clip, for reference:\n{judge_words(fixing.ruling)}"
        )
    else:
        failed = f"What failed:\n{judge_words(fixing.ruling)}"
    return "\n\n".join(
        [
            f"The ad is for this product page: {job.product_url}",
            f"You fix scene {scene.number}.",
            failed,
            sent_list_words(sent) if sent is not None else NO_SENT_LIST,
            _library_words(job),
            "How this product behaves, as the planner wrote it from the page: "
            + (job.product_behaviour or "the planner wrote no note for this ad."),
            f"The whole plan, scene by scene:\n{plan}",
            # A sent list holds its prompt.
            *(
                []
                if sent is not None
                else [f"Scene {scene.number}'s video prompt was:\n{work.motion_of(first)}"]
            ),
            f"Scene {scene.number}'s line takes {seconds:.2f} s to say, so its clip is "
            f"{clip_seconds} s.",
            f"Shop photos, any of which may be Image 1: {every}; those showing the product in "
            f"the ad's colour, {job.product_colour}: {colour}. For a new start picture, pick "
            "Image 1 from those in the ad's colour; only when none suits, or there are none, "
            "take another where the product is clearly seen. The product's look and colour come "
            f"only from that photo: never change them or make one up. {scenes.SEVERAL_ITEMS}",
            f'Code starts the video prompt with: "{opener}" then your words.',
            f"The page's words:\n{job.page_text}",
        ]
    )


def sent_list_words(sent: library.SentList, whose: str = "the failed clip") -> str:
    """What a clip was sent, as a list in words: each picture with its library number and
    label, each piece with its video and moment, what was left out and why, and the prompt."""
    lines = [f"What {whose} was sent, every picture in {sent.colour}:"]
    for picture in sent.pictures:
        named = (
            f"library item {picture.library_number}"
            if picture.library_number is not None
            else DRAWN[picture.drawn or "room"]
        )
        lines.append(
            f"Image {picture.image}: {named}. {picture.label}"
            + (f" (for {picture.for_})" if picture.for_ else "")
        )
    for piece in sent.pieces:
        cut = (
            ""
            if piece.trim is None
            else f", cut from its {piece.moment_start:g} s to {piece.moment_end:g} s because "
            f"{clip_judge.TRIMMED.get(piece.trim, piece.trim)}"
        )
        lines.append(
            f"Video {piece.number}: page video {piece.video}, moment {piece.moment}, "
            f"{piece.start:g} s to {piece.end:g} s{cut}. {PIECE_LABEL}"
        )
    for left in sent.left_out:
        named = (
            f"library item {left.library_number}"
            if left.library_number is not None
            else DRAWN[left.drawn or "room"]
        )
        lines.append(f"Left out: {named}. {left.label} ({LEFT_OUT_FOR[left.tier]})")
    lines += [
        f"Library item {each.of} couldn't be made {each.colour}, so it went as it was."
        for each in sent.recolours
        if each.outcome == "fell_back"
    ]
    lines.append(f"The prompt it was sent: {sent.prompt}")
    return "\n".join(lines)


# Why a picture was left out of a clip, by its tier (spec #210 point 13): every tier past the
# video API's cap of pictures.
LEFT_OUT_FOR = {
    tier: f"past the video model's {library.VIDEO_API_MOST_PICTURES} pictures, {what}"
    for tier, what in {
        1: "though it was the front look photo",
        2: "though it was a pick for the scene",
        3: "though it was the drawn picture",
        4: "another side photo",
    }.items()
}


def _library_words(job: Job) -> str:
    """The ad's library as a text list, one line per item and no pictures, then each page
    video's moments: what look_at_library opens by number."""
    listed = library_items.library_text(job) or "(no items)"
    moments = [
        f"Page video {video.position}, moment {moment.number}, {moment.start:g} s to "
        f"{moment.end:g} s: {moment.happens} On screen: {moment.on_screen}"
        for video in job.videos.order_by("position")
        if (watch := library.watch_of(video)) is not None
        for moment in watch.moments
    ]
    return (
        "The ad's picture library, one line per item, which look_at_library opens by its "
        f"number:\n{listed}"
        + (
            "\n\nThe page videos' moments, which look_at_library opens by video and moment:\n"
            + "\n".join(moments)
            if moments
            else ""
        )
    )


def _scene_facts(scene: Scene) -> str:
    facts = [f"Scene {scene.number}", f'  line: "{scene.line}"']
    if scene.shows:
        facts += [
            f"  shows: {scene.shows}",
            f"  kind: {scene.broll_kind}; face: {scene.person_shown}",
            *([f"  how the product is used: {scene.usage}"] if scene.usage else []),
            *([f"  ends on: {scene.result}"] if scene.result else []),
        ]
    else:
        facts.append("  the presenter says it to camera")
    return "\n".join(facts)


def judge_words(ruling: dict[str, Any] | None) -> str:
    """What the judge found, in words, for the agent."""
    if not ruling:
        return "No clip was judged."
    verdict = ruling.get("verdict", "not judged")
    lines = [f"The judge's verdict: {verdict}. Checks: {json.dumps(ruling.get('checks', {}))}."]
    if verdict == "fail":
        # A PRODUCT fail in its fixed form first, then every other failed check's faults.
        fixed_form = _product_faults(ruling)
        others = (
            {**ruling, "checks": {**ruling.get("checks", {}), "product": "pass"}}
            if fixed_form
            else ruling
        )
        lines += fixed_form + [
            f"- It found: {fault}" for fault in dict.fromkeys(auto_fix.faults(others))
        ]
    motion = (ruling.get("answers") or {}).get("motion") or []
    if motion and isinstance(motion[0], dict):
        first = motion[0]
        if first.get("seen_action"):
            lines.append(f"What it saw happen: {first['seen_action']}")
        moments = (first.get("look") or {}).get("moments") or []
        if moments:
            lines.append(
                "Second by second: "
                + " ".join(f"{m['time']:g}s {m['what_happens']}" for m in moments)
            )
    return "\n".join(lines)


def _product_faults(ruling: dict[str, Any]) -> list[str]:
    """Each fault the judge's PRODUCT check failed the clip for, in its fixed form (#228's
    ProductVerdict): the reference it compared against, what the clip shows, what the real
    product shows, at which seconds, and the fault in product terms. None for a ruling with
    no PRODUCT fail, whose faults are said as found."""
    if (ruling.get("checks") or {}).get("product") != "fail":
        return []
    lines = []
    for answer in (ruling.get("answers") or {}).get("product") or []:
        if not answer or answer.get("verdict") != "fail":
            continue
        times = ", ".join(f"{t:g} s" for t in answer.get("fault_frame_times") or [])
        line = (
            f"- Compared against {answer.get('compared_against') or 'the references'}: the clip "
            f"shows {answer.get('clip_shows') or 'a fault'}; the real product shows "
            f"{answer.get('real_product_shows') or 'something else'}"
            + (f" (at {times})." if times else ".")
        )
        if answer.get("fault"):
            line += f" The fault: {answer['fault']}"
        if line not in lines:
            lines.append(line)
    return lines


def _joined(keys: list[str]) -> Callable[[], bytes]:
    return lambda: clip_judge.joined([PIL.Image.open(io.BytesIO(file_store.read(k))) for k in keys])


def _frames(clip: ProducedItem, label: str) -> list[Shown]:
    """The clip's frames as the judge saw them for movement, 5 a second, joined 4 to a
    picture so a turn stays under MOST_PICTURES."""
    kept = ((clip.ruling or {}).get("pictures") or {}).get("frames")
    if not kept:
        kept = clip_judge.cut_pictures(clip.file, beside=0, sets=("frames",))["frames"]
    return _four_to_a_picture(kept, label)


def _four_to_a_picture(kept: list[dict[str, Any]], label: str) -> list[Shown]:
    """Frames, each with its times, joined 4 to a picture under a name made from them, so the
    same frames are the same picture on every turn."""
    shown = []
    for i in range(0, len(kept), 4):
        group = kept[i : i + 4]
        times = [t for each in group for t in each["times"]]
        keys = [each["key"] for each in group]
        key = file_store.save_once(
            f"fixer_frames_{uuid.uuid5(uuid.NAMESPACE_URL, '|'.join(keys)).hex}.jpg",
            _joined(keys),
            whole=lambda data: len(data) > 0,
        )
        shown.append(Shown(label=f"{label}, {clip_judge.frame_label(times)}", key=key))
    return shown


# ---------------------------------------------------------------- the tools


def _fixing_of(call: ToolCall) -> AgentFix:
    assert call.agent_fix is not None, "the fixing agent's calls belong to its fixing"
    return call.agent_fix


class LookAtShopPhoto(Tool):
    """See one of the shop's product photos, by its number."""

    name: ClassVar[str] = "look_at_shop_photo"
    photo: int

    def run(self, call: ToolCall) -> ToolResult:
        photo = _fixing_of(call).step.scene.job.photos.filter(position=self.photo).first()
        if photo is None:
            raise Refused(f"there is no photo {self.photo}.")
        return ToolResult(
            f"Shop photo {self.photo}.", [Image(label=f"Shop photo {self.photo}", key=photo.file)]
        )


class LookAtPlace(Tool):
    """See the place this scene happens in: its own newest one, when place_edit made it one,
    else the ad's place picture, the place every B-roll scene happens in."""

    name: ClassVar[str] = "look_at_place"

    def run(self, call: ToolCall) -> ToolResult:
        scene = _fixing_of(call).step.scene
        own = work.newest_own_place(scene)
        if own is not None:
            named = f"Scene {scene.number}'s own {PLACE_NAMES[_drawn_as(own)]}"
            return ToolResult(f"{named}.", [Image(label=named, key=own.file)])
        place = work.latest(scene.job, ProducedItem.Kind.BEFORE_PICTURE)
        if place is None:
            raise Refused("the ad has no place picture.")
        return ToolResult("The place picture.", [Image(label="The place picture", key=place.file)])


def _drawn_as(own: ProducedItem) -> Literal["person_in_room", "room"]:
    """Which picture of a scene's place `own` is."""
    return "person_in_room" if own.kind == ProducedItem.Kind.SCENE_PERSON_IN_ROOM else "room"


class LookAtPresenter(Tool):
    """See the presenter's portrait: the person whose face is shown in a "has face" scene."""

    name: ClassVar[str] = "look_at_presenter"

    def run(self, call: ToolCall) -> ToolResult:
        portrait = work.latest(_fixing_of(call).step.scene.job, ProducedItem.Kind.PORTRAIT)
        if portrait is None:
            raise Refused("the ad has no presenter yet.")
        return ToolResult(
            "The presenter.", [Image(label="The presenter's portrait", key=portrait.file)]
        )


class LookAtTry(Tool):
    """See a try again: what its clip was sent (the pictures with their labels and the video
    pieces' frames), or its start picture for a clip made from one, then its clip's frames
    and the judge's words. Try 0 is the scene as it failed in the ad."""

    name: ClassVar[str] = "look_at_try"
    number: int

    def run(self, call: ToolCall) -> ToolResult:
        fixing = _fixing_of(call)
        parts = try_parts(fixing, self.number)
        if parts is None:
            raise Refused(f"tries so far: 0 to {fixing.tries.count()}.")
        picture, clip, ruling = parts
        label = f"Try {self.number}"
        sent, shown = try_shown(label, picture, clip)
        said = (
            f"{label}: what its clip was sent, then the clip's frames. {sent}"
            if sent
            else f"{label}."
        )
        return ToolResult(f"{said} {judge_words(ruling)}", shown)


def try_parts(
    fixing: AgentFix, number: int
) -> tuple[ProducedItem | None, ProducedItem | None, dict[str, Any] | None] | None:
    """Try `number` of `fixing` as its start picture, its clip and the judge's ruling on it,
    or None when it has no such try. Try 0 is the clip it fixes, with the ruling handed over."""
    if number == 0:
        first = fixing.first_clip
        return (first.picture if first else None, first, fixing.ruling)
    row = fixing.tries.filter(number=number).first()
    if row is None:
        return None
    return row.picture, row.clip, row.clip.ruling if row.clip is not None else None


def try_shown(
    label: str, picture: ProducedItem | None, clip: ProducedItem | None
) -> tuple[str, list[Image]]:
    """What a look at a try shows, and what code says of what its clip was sent: when its
    clip carries a sent list (spec #210 point 21), the pictures it was sent with their labels
    and each piece's frames, then the clip's frames; else its start picture, then the clip's
    frames, with nothing said."""
    sent = library.sent_list_of(clip) if clip is not None else None
    frames = [] if clip is None else _frames(clip, f"{label}, clip")
    clip_frames = [Image(label=each.label, key=each.key) for each in frames]
    if sent is not None:
        # Each piece's frames are joined as the clip's are, so a try's pictures leave room
        # for another's in MOST_PICTURES.
        pieces = [
            Image(label=each.label, key=each.key)
            for piece in sent.pieces
            for each in _four_to_a_picture(
                clip_judge.piece_frames(piece.file), f"{label}, Video {piece.number}"
            )
        ]
        return sent_list_words(sent, f"{label}'s clip"), [
            *clip_judge.sent_pictures(sent, label),
            *pieces,
            *clip_frames,
        ]
    shown = [Image(label=f"{label}, start picture", key=picture.file)] if picture else []
    return "", [*shown, *clip_frames]


class LookAtLibrary(Tool):
    """Open one item of the ad's picture library by its number, to see the picture; or one
    moment of a page video, by the video's number and the moment's, to see its frames. What
    you open stays in front of you for the rest of this scene's fix."""

    name: ClassVar[str] = "look_at_library"
    item: int | None = Field(description="The library item's number; null for a moment.")
    video: int | None = Field(description="For a moment: the page video's number; else null.")
    moment: int | None = Field(description="For a moment: its number in that video; else null.")

    def run(self, call: ToolCall) -> ToolResult:
        job = _fixing_of(call).step.scene.job
        an_item = self.item is not None and self.video is None and self.moment is None
        a_moment = self.item is None and self.video is not None and self.moment is not None
        if not (an_item or a_moment):
            raise Refused("name a library item, or a video and its moment, not both.")
        if self.item is not None:
            found = [each for each in library.library_of(job) if each.number == self.item]
            if not found:
                raise Refused(f"there is no library item {self.item}.")
            [item] = found
            return _within_the_room(
                call,
                ToolResult(
                    f"Library item {library_items.library_line(item)}",
                    [Image(label=f"Library item {item.number}: {item.label}", key=item.file)],
                ),
            )
        assert self.video is not None and self.moment is not None
        video, _, moment = _moment(job, self.video, self.moment)
        named = f"Page video {self.video}, moment {self.moment}"
        part = product_videos.cut_part(video.file, moment.start, moment.end, sound=False)
        # Each frame at its second in the video, so it reads as the moment's seconds do.
        frames = [
            {**frame, "times": [round(moment.start + t, 3) for t in frame["times"]]}
            for frame in clip_judge.piece_frames(part)
        ]
        return _within_the_room(
            call,
            ToolResult(
                f"{named}, {moment.start:g} s to {moment.end:g} s: {moment.happens} On screen: "
                f"{moment.on_screen} Its frames, each at its second in the video, "
                f"{PIECE_LABEL}.",
                [
                    Image(label=each.label, key=each.key)
                    for each in _four_to_a_picture(frames, named)
                ],
            ),
        )


def _within_the_room(call: ToolCall, opened: ToolResult) -> ToolResult:
    """`opened`, unless its pictures would take the library pictures the agent has open past
    LIBRARY_ROOM: then refused, in words. A look at one already open is always let through,
    as it is shown once."""
    fixing = _fixing_of(call)
    looks = {
        tuple(picture["key"] for picture in each.pictures)
        for each in _calls(fixing).filter(tool=LookAtLibrary.name, finished_at__isnull=False)
        if each.pictures
    }
    keys = tuple(picture.key for picture in opened.pictures)
    if keys in looks:
        return opened
    if sum(len(each) for each in looks) + len(keys) > _library_room():
        raise Refused(LIBRARY_FULL)
    return opened


def _moment(job: Job, video: int, moment: int) -> tuple[ProductVideo, library.VideoWatch, Moment]:
    """Moment `moment` of page video `video`, with the video and what the watcher saw in it;
    refused when the library holds no such moment."""
    found = job.videos.filter(position=video).first()
    watch = library.watch_of(found) if found is not None and found.file else None
    if found is None or watch is None:
        raise Refused(f"there is no watched page video {video}.")
    moments = [each for each in watch.moments if each.number == moment]
    if not moments:
        raise Refused(f"page video {video} has no moment {moment}.")
    return found, watch, moments[0]


class NamedPicture(BaseModel):
    """A library item a try is sent, by its number."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    number: int = Field(description="The library item's number.")
    used_for: library.For = Field(
        description="What the clip takes from it: content, result, in-use, place, person or state."
    )


class NamedPiece(BaseModel):
    """A page video's moment a try is sent as its video piece, by number."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    video: int = Field(description="The page video's number.")
    moment: int = Field(description="The moment's number in that video.")


class References(BaseModel):
    """What a try is sent in place of a start picture."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pictures: list[NamedPicture] = Field(
        description="The library items to send, in the order they go: add, remove or swap "
        "items by number. At least one."
    )
    piece: NamedPiece | None = Field(
        description="The page video moment to send as the piece, for the movement; or null."
    )


class PlaceEdit(Tool):
    """Change where this scene happens, for this scene only: the other scenes keep the ad's
    place. Either make a small edit of the scene's picture of its place (edit), or have a new
    place drawn fresh (new_place). The small edit is cheaper and keeps the look already
    approved. A scene showing the presenter's face is sent the presenter in the room; one
    showing nobody, the room alone. You are shown the new picture and nothing is filmed:
    look at it, change it again, or make a filmed move (film_again, change_action or
    end_result_only), which sends it."""

    name: ClassVar[str] = "place_edit"
    picture: Literal["person_in_room", "room"] = Field(
        description="Which picture of the place: the presenter in the room, or the room alone."
    )
    edit: str | None = Field(
        description="A small change to the scene's picture of its place, in your words, such "
        "as what to clear away or move; null for a new place."
    )
    new_place: str | None = Field(
        description="The new place, described in your words, to be drawn fresh; null for an edit."
    )

    def run(self, call: ToolCall) -> ToolResult:
        return place_edit(_fixing_of(call), self)


# What place_edit calls each picture of a scene's place it makes.
PLACE_NAMES = {
    "person_in_room": "picture of the presenter in its place",
    "room": "picture of its place on its own",
}


def place_edit(fixing: AgentFix, asked: PlaceEdit) -> ToolResult:
    """The scene's own picture of its place, made as the agent wrote it: an edit of the one it
    has now, or a new place drawn fresh (#237). Shown to the agent; nothing films."""
    words = [each for each in (asked.edit, asked.new_place) if each is not None]
    if len(words) > 1:
        raise Refused("write an edit or a new place, not both.")
    if not words:
        raise Refused("write an edit or a new place.")
    if not words[0].strip():
        raise Refused("the words are blank: write the edit or the new place.")
    scene = fixing.step.scene
    try:
        made = (
            [work.edit_scene_place(scene, asked.picture, asked.edit)]
            if asked.edit is not None
            else work.new_scene_place(scene, asked.picture, words[0])
        )
    except work.NoPlaceYet as missing:
        raise Refused(f"the ad has no {PLACE_NAMES[asked.picture]} yet.") from missing
    except work.NoPresenterYet as missing:
        raise Refused("the ad has no presenter yet, so nobody can be drawn in it.") from missing
    named = f"Scene {scene.number}'s own {PLACE_NAMES[asked.picture]}"
    return ToolResult(
        f"{named}, for this scene only: the other scenes keep the ad's. Nothing was filmed: "
        "look at it, change it again, or make a filmed move (film_again, change_action or "
        "end_result_only), which sends it.",
        [Image(label=named, key=made[-1].file)],
    )


class FilmAgain(Tool):
    """Film this scene again as you write it here: code saves your scene row, draws a new
    start picture when you ask for one and checks it, or sends the references you name in its
    place, makes the clip and has the judge watch it. Write the whole scene as it should be,
    every field."""

    name: ClassVar[str] = "film_again"
    root_cause: str = Field(
        description="In plain words: what caused the last fail (the plan, the pictures, the "
        "prompt, or what the video model can't do)."
    )
    move: str = Field(description="In plain words: what you change to fix that cause, and why.")
    line: str = Field(
        description="The scene's line, word for word: the same one, or a new true one."
    )
    shows: str = Field(description="What the scene shows: the one action, in one sentence.")
    result: str = Field(description='What it ends on, for "does a job"; empty otherwise.')
    broll_kind: Literal["does a job", "showcase", "shows the problem"]
    person_shown: Literal["no face", "has face"]
    new_picture: bool = Field(
        description="True to draw a new start picture from picture_prompt; false keeps the "
        "current one."
    )
    photo: int | None = Field(
        description="With a new picture: the shop photo that is Image 1, one in the ad's colour "
        "when one suits."
    )
    picture_prompt: str | None = Field(
        description="With a new picture: the picture model's prompt, written from scratch, "
        "naming each image by its number and job."
    )
    video_prompt: str = Field(
        description="The video model's prompt, from scratch, positive orders only. Code adds "
        "the clip's length and the still camera before it; with references, the speed line "
        "and a label for each picture and the piece instead."
    )
    would_ask_owner: str | None = Field(
        description="What you would ask the shop owner, if anything. Nobody is asked."
    )
    references: References | None = Field(
        default=None,
        description="The library pictures and the moment to send in place of the start "
        "picture, with new_picture false. Code recolours them to the clip's colour and labels "
        "them. Null films from the start picture.",
    )

    def run(self, call: ToolCall) -> ToolResult:
        return film_again(_fixing_of(call), self, call)


class ChangeAction(FilmAgain):
    """Film another selling action in place of the one that failed: you write the changed
    scene yourself, every field, its line and what it shows included. Code fact checks it
    against the page, voices a new line first, then films it as film_again does and has the
    judge watch it."""

    name: ClassVar[str] = "change_action"
    shows: str = Field(description="The new action, in one sentence: another selling action.")

    def run(self, call: ToolCall) -> ToolResult:
        fixing = _fixing_of(call)
        # Checked before the try only: once filmed, the scene's action is this call's own.
        if not fixing.tries.filter(tool_call=call).exists() and _same_words(
            self.shows, _last_filmed(fixing)["shows"]
        ):
            raise Refused(SAME_ACTION)
        return film_again(fixing, self, call)


# What change_action is told when it keeps the action the last clip filmed.
SAME_ACTION = (
    "change_action films another action, and this is the one the last clip filmed: write "
    "another selling action in shows, or call film_again to film this one again."
)


def _last_filmed(fixing: AgentFix) -> dict[str, Any]:
    """The scene row the newest clip of the fixing was filmed from: its newest try's, or the
    scene's as it was before try 1."""
    last = fixing.tries.filter(clip__isnull=False).order_by("-number").first()
    row: dict[str, Any] = last.scene_row if last is not None else fixing.scene_before
    return row


def _same_words(one: str, other: str) -> bool:
    return " ".join(one.split()).lower() == " ".join(other.split()).lower()


# The fixed line code puts before the agent's video prompt in end_result_only.
END_RESULT_ONLY = (
    "The clip shows only the end result: from its first frame the action is already done, "
    "and the clip holds on that result as it stands."
)


class EndResultOnly(FilmAgain):
    """Film only the end state the action leads to, with the action already done: you write
    the scene yourself, every field, with what it shows as that end state. Its start picture
    shows that end state too, so draw a new one unless the current one already shows it. Code
    adds a fixed line saying so before your video prompt, then films it as film_again does and
    has the judge watch it."""

    # The clip's first frame is a redraw of its start picture, until the start picture is
    # retired (spec #210 point 19): so here the picture shows the end state, not the moment
    # before the action.
    name: ClassVar[str] = "end_result_only"
    shows: str = Field(
        description="The end state the clip holds on, in one sentence: the product and what "
        "the action left."
    )
    new_picture: bool = Field(
        description="True to draw a new start picture of the end state from picture_prompt; "
        "false only when the current one already shows that end state."
    )
    picture_prompt: str | None = Field(
        description="With a new picture: the picture model's prompt, written from scratch, "
        "showing the end state with the action already done, naming each image by its number "
        "and job."
    )

    def run(self, call: ToolCall) -> ToolResult:
        told = self.model_copy(update={"video_prompt": f"{END_RESULT_ONLY} {self.video_prompt}"})
        return film_again(_fixing_of(call), told, call)


class GiveUp(Tool):
    """Give up on filming this scene: its last try stays in the ad, and the shop owner is
    shown every try. Say why, and which try you think is best."""

    name: ClassVar[str] = "give_up"
    reason: str
    best_try: int | None = Field(
        default=None,
        description="The try you think is best, marked for the shop owner, who may pick any.",
    )

    def run(self, call: ToolCall) -> str:
        fixing = _fixing_of(call)
        filmed = list(fixing.tries.filter(clip__isnull=False).values_list("number", flat=True))
        if not filmed:
            raise Refused("try to film it at least once before giving up.")
        if self.best_try is not None and self.best_try not in filmed:
            raise Refused(f"best_try is one of the tries filmed: {filmed}.")
        fixing.gave_up = self.reason
        fixing.picked = self.best_try
        fixing.save(update_fields=["gave_up", "picked"])
        return GIVEN_UP[_gives_up_as(fixing)]


class AskOwner(Tool):
    """Your last resort: ask the shop owner for what this fix needs, only when the ad's library
    holds nothing for it: a video, a photo, or words. Write the question yourself, plainly,
    for the shop owner. The fix waits for the answer, the last try staying in the ad
    meanwhile; the answer comes back here, with the new library item it made. You may ask
    once in a scene's fix."""

    name: ClassVar[str] = "ask_owner"
    question: str = Field(min_length=1, description="Your question for the shop owner.")
    wants: Literal["video", "photo", "words"] = Field(description="What you ask them for.")

    def run(self, call: ToolCall) -> str:
        # An ask that may be made never runs here: it parks the fix (`_park`).
        raise Refused(_why_it_may_not_ask(_fixing_of(call)) or "the ask can't be made.")


TOOLS: list[type[Tool]] = [
    LookAtShopPhoto,
    LookAtPlace,
    LookAtPresenter,
    LookAtTry,
    LookAtLibrary,
    PlaceEdit,
    FilmAgain,
    ChangeAction,
    EndResultOnly,
    GiveUp,
    AskOwner,
]

AGENT_SPEC = loop.Agent(name=AGENT, purpose=PURPOSE, instructions="", tools=TOOLS)


# ---------------------------------------------------------------- one try


def _row(scene: Scene) -> dict[str, Any]:
    return {field: getattr(scene, field) for field in ROW}


def _owner_photos(step: SceneStep, use: str) -> list[ReferencePhoto]:
    return [row for row in step.reference_photos.all() if row.use == use]


def _pictures_sent(
    fixing: AgentFix, person_shown: str, photo: int
) -> tuple[list[str], list[dict[str, Any]]]:
    """The pictures the picture model gets, as the app sends them: the shop owner's things
    first, then the main photo, the scene's needs photos, the portrait for a face, the place
    last: the scene's own, when place_edit made it one, else the ad's before-picture.
    Gives the files and the pictures_sent rows."""
    step = fixing.step
    job = step.scene.job
    owners = _owner_photos(step, ReferencePhoto.Use.PICTURE)
    files = [work.clean_picture(step.scene, row) for row in owners]
    rows: list[dict[str, Any]] = [
        {"image": number, "job": reference_photos.picture_job(row.thing), "reference_photo": row.pk}
        for number, row in enumerate(owners, 1)
    ]
    rows.append({"image": len(rows) + 1, "job": scenes.MAIN_PHOTO_JOB, "photo": photo})
    files.append(job.photos.get(position=photo).file)
    faces = {p.position: p.has_face for p in job.photos.all()}
    for need in scenes.photos_for_needs(step.scene.needs, faces, scenes.MOST_NEEDS_PHOTOS):
        files.append(job.photos.get(position=need.photo).file)
        rows.append({"image": len(rows) + 1, "job": scenes.need_job(need), "photo": need.photo})
    if person_shown == Scene.PersonShown.HAS_FACE:
        portrait = work.latest(job, ProducedItem.Kind.PORTRAIT)
        assert portrait is not None, "a scene with a face has its presenter"
        files.append(portrait.file)
        rows.append({"image": len(rows) + 1, "job": scenes.PORTRAIT_JOB, "portrait": True})
    # The scene's own place, when place_edit made it one (#237), else the ad's.
    own = work.own_place(step.scene, work.drawn_for(person_shown))
    if own is not None:
        files.append(own.file)
        rows.append(
            {"image": len(rows) + 1, "job": scenes.BEFORE_PICTURE_JOB, "scene_place": own.pk}
        )
        return files, rows
    place = work.latest(job, ProducedItem.Kind.BEFORE_PICTURE)
    assert place is not None, "every B-roll picture is drawn in the ad's place"
    files.append(place.file)
    rows.append({"image": len(rows) + 1, "job": scenes.BEFORE_PICTURE_JOB, "before_picture": True})
    return files, rows


def _current(fixing: AgentFix) -> tuple[ProducedItem, ProducedItem]:
    """The start picture and the line's audio the scene's newest try was filmed from."""
    last = fixing.tries.filter(clip__isnull=False).order_by("-number").first()
    clip = last.clip if last is not None else fixing.first_clip
    assert clip is not None and clip.picture is not None and clip.made_from is not None
    return clip.picture, clip.made_from


def _try_step(fixing: AgentFix, call: ToolCall, kind: str, **made: Any) -> SceneStep:
    """A step of a try. It runs here, inside the scene step the agent fixes, never as a
    step of its own in the background: so it is never "running", which only that one is."""
    step = SceneStep.objects.create(
        scene=fixing.step.scene,
        kind=kind,
        status=SceneStep.Status.FINISHED,
        tool_call=call,
        fix=fixing.step.fix,
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


def film_again(fixing: AgentFix, asked: FilmAgain, call: ToolCall) -> ToolResult:
    """One try at the scene. A refusal, or work that fails, puts the scene row back as it
    was before the try. Run again after a worker stopped, it pays for nothing twice."""
    row = fixing.tries.filter(tool_call=call).first()
    if row is not None and row.step is None:
        return ToolResult(f"Try {row.number} ended with no clip: its picture kept failing.", [])
    if row is not None and row.clip is not None and "verdict" in (row.clip.ruling or {}):
        return _told(fixing, row)
    scene = fixing.step.scene
    scene.refresh_from_db()
    before = {field: getattr(scene, field) for field in KEPT}
    try:
        told = _film_again(fixing, asked, call, scene, row)
    except BaseException:
        _put_back(scene, before)
        raise
    if fixing.tries.filter(tool_call=call, step__isnull=True).exists():
        # Its picture kept failing: nothing was filmed, so the scene is as it was.
        _put_back(scene, before)
    return told


def _refuse_what_breaks_the_rules(fixing: AgentFix, asked: FilmAgain, scene: Scene) -> None:
    # A new try past the cap, as a worker restarted mid-turn could start: the fixing ends at
    # the cap, so it is refused as any call after the end is.
    if fixing.tries.count() >= fixing.cap:
        raise Refused(ENDED)
    if not asked.shows.strip():
        raise Refused(NO_TALKING)
    found = sorted({match.group(0).lower() for match in BANNED.finditer(asked.video_prompt)})
    if found:
        raise Refused(f"the video prompt has bans ({', '.join(found)}). Say what happens instead.")
    if asked.references is not None:
        _refuse_bad_references(scene.job, asked)
        if asked.broll_kind != "does a job" and asked.result.strip():
            raise Refused('only a "does a job" scene has a result.')
        return
    if asked.new_picture and not (asked.photo and asked.picture_prompt):
        raise Refused("a new picture needs photo and picture_prompt.")
    every = list(scene.job.photos.values_list("position", flat=True))
    if asked.new_picture and asked.photo not in every:
        raise Refused(f"Image 1 must be one of the shop photos: {every}.")
    if not asked.new_picture and asked.person_shown != scene.person_shown:
        raise Refused("showing or hiding the face changes the picture: draw a new one.")
    if asked.broll_kind != "does a job" and asked.result.strip():
        raise Refused('only a "does a job" scene has a result.')
    _refuse_what_misses_the_place(fixing, asked, scene)


def _refuse_what_misses_the_place(fixing: AgentFix, asked: FilmAgain, scene: Scene) -> None:
    """Refuse a try that wouldn't be filmed in the scene's own newest place (#237): one whose
    face needs the other picture of it, or one keeping a start picture drawn before it."""
    newest = work.newest_own_place(scene)
    if newest is None:
        return
    drawn = work.drawn_for(asked.person_shown)
    if work.own_place(scene, drawn) is None:
        raise Refused(
            f"the scene's newest place is its own {PLACE_NAMES[_drawn_as(newest)]}, and this "
            f"try needs its {PLACE_NAMES[drawn]}: call place_edit with picture {drawn} first."
        )
    if not asked.new_picture and newest.pk > _current(fixing)[0].pk:
        raise Refused(
            "the scene's place changed after its start picture was drawn: draw a new start "
            "picture, so the try is filmed in its new place."
        )


# A move code refuses (references the library doesn't hold, a rule broken) comes back as the
# tool's answer and costs no clip and no try: today's behaviour for rule refusals, kept and
# named here (audit item 20, not approved yet).
def _refuse_bad_references(job: Job, asked: FilmAgain) -> None:
    """Refuse references that name what the library doesn't hold, before anything is made."""
    references = asked.references
    assert references is not None
    if asked.new_picture:
        raise Refused(
            "a try sent references is filmed from them, not from a start picture: set "
            "new_picture to false."
        )
    if not references.pictures:
        raise Refused("name at least one picture: a video piece goes beside pictures.")
    held = {each.number for each in library.library_of(job)}
    named: set[int] = set()
    for picture in references.pictures:
        if picture.number not in held:
            raise Refused(f"there is no library item {picture.number}.")
        if picture.number in named:
            raise Refused(f"library item {picture.number} is named twice.")
        named.add(picture.number)
    if references.piece is not None:
        _moment(job, references.piece.video, references.piece.moment)


def _film_again(
    fixing: AgentFix, asked: FilmAgain, call: ToolCall, scene: Scene, row: AgentFixTry | None
) -> ToolResult:
    if row is None:
        _refuse_what_breaks_the_rules(fixing, asked, scene)
    number = row.number if row is not None else fixing.tries.count() + 1
    wanted = {
        "line": " ".join(asked.line.split()),
        "shows": asked.shows.strip(),
        "result": asked.result.strip(),
        "broll_kind": asked.broll_kind,
        "person_shown": asked.person_shown,
    }
    changed = [field for field in ROW if wanted[field] != getattr(scene, field)]

    # The scene row, fact checked when the line or what it shows changed.
    if changed:
        scene.change_line(
            wanted["line"],
            wanted["shows"],
            broll={field: wanted[field] for field in ("result", "broll_kind", "person_shown")},
        )
        if {"line", "shows", "result"} & set(changed):
            _fact_checked(scene)
        scene.save()

    # The new line is voiced first, so a line refused costs no picture.
    picture, audio = _current(fixing)
    if row is not None and row.step is not None and row.step.made_from is not None:
        audio = row.step.made_from
    elif "line" in changed:
        audio = _voiced(fixing, call)
    shown: list[Image] = []
    if row is not None and row.picture is not None:
        picture = row.picture
    elif asked.new_picture:
        drawn = _drawn(fixing, asked, call, number, shown)
        if isinstance(drawn, str):
            return ToolResult(drawn, shown)
        picture = drawn

    # What a try with references is sent: built once, before its try is, and kept on it.
    sent = (
        library.SentList.model_validate(row.sent)
        if row is not None and row.sent is not None
        else _references_sent(fixing, asked.references, asked.video_prompt, asked.person_shown)
        if row is None and asked.references is not None
        else None
    )

    if row is None:
        step = _try_step(
            fixing,
            call,
            SceneStep.Kind.CLIP,
            made_from=audio,
            picture=picture,
            picture_step=picture.step,
            motion_prompt=asked.video_prompt,
        )
        row = AgentFixTry.objects.create(
            agent_fix=fixing,
            number=number,
            tool_call=call,
            step=step,
            picture=picture,
            scene_row=_row(scene),
            new_picture=asked.new_picture,
            picture_prompt=asked.picture_prompt or "",
            video_prompt=sent.prompt
            if sent is not None
            else _as_sent(step, picture, audio, asked.video_prompt),
            sent=sent.model_dump(mode="json") if sent is not None else None,
            root_cause=asked.root_cause,
            move=asked.move,
            would_ask_owner=asked.would_ask_owner or "",
        )
    assert row.step is not None
    step = row.step
    try:
        clip = step.produced.filter(kind=ProducedItem.Kind.CLIP).first() or work._make_clip(
            step, motion=asked.video_prompt, sent=sent
        )
        if sent is not None and clip.sent is None:
            # Made before the worker stopped, but not yet holding what it was sent.
            library.keep_sent_list(clip, sent)
        ProducedItem.objects.filter(pk=clip.pk).update(remake=number, change=f"try {number}")
        clip.refresh_from_db()
        work._judge_clip(step, clip)
    except BaseException as error:
        _failed(step, str(error))
        row.delete()
        raise
    _done(step)
    clip.refresh_from_db()
    row.clip = clip
    if fixing.door == AgentFix.Door.OWNER:
        settled = work._the_clip_judges_answer(clip)
        row.fix_check = {"verdict": settled.verdict, "reason": settled.reason}
    row.passed = _passes(row, clip)
    row.save(update_fields=["clip", "fix_check", "passed"])
    fixing.redraws = 0
    if row.passed:
        fixing.passed = clip
    fixing.save(update_fields=["redraws", "passed"])
    return _told(fixing, row, changed=changed, shown=shown)


# What the clip takes from a picture a try sends, by its `for` (jobs.library.For), as code's
# label line says it.
USED_FOR: dict[str, str] = {
    "content": "what the product takes in or acts on",
    "result": "how the scene ends",
    "in-use": "how the product is held or used",
    "place": "where it happens",
    "person": "who does it",
    "state": "how the product is in this scene",
}


def _references_sent(
    fixing: AgentFix, references: References, prompt: str, person_shown: str
) -> library.SentList:
    """The sent list of a try with references (spec #210 point 23): cut-and-send's, the
    agent's picks as its tier 2 and the moment named as its piece, so code orders, recolours,
    caps and says in red what was left out as for every clip; then the prompt: the speed
    line, a label line per picture and piece, and the agent's own words."""
    scene = fixing.step.scene
    sending = cut_and_send.cut_and_send(
        scene,
        person_shown=person_shown,
        picks=[
            ScenePicture(number=picture.number, for_=picture.used_for)
            for picture in references.pictures
        ],
        pieces=[]
        if references.piece is None
        else [ScenePiece(video=references.piece.video, moment=references.piece.moment)],
    )
    made_now = {each.made for each in sending.recolours if each.made is not None}
    lines = [
        f"Image {picture.image} is {drawn_label(scene.job, picture.drawn)}."
        if picture.drawn is not None
        else f"Image {picture.image}"
        + (f", made {sending.colour}" if picture.library_number in made_now else "")
        + f": {picture.label}"
        + (f" Used for {USED_FOR[picture.for_]}." if picture.for_ else "")
        for picture in sending.pictures
    ]
    lines += [f"Video {piece.number} is {PIECE_LABEL}." for piece in sending.pieces]
    return sending.sent_list("\n".join([SPEED_LINE, *lines, prompt.strip()]))


def _as_sent(step: SceneStep, picture: ProducedItem, audio: ProducedItem, motion: str) -> str:
    """The video prompt as it is sent: code's opener, then the agent's words."""
    handoff = work._clip_handoff(step, picture, audio, motion=motion)
    assert isinstance(handoff, BrollClipHandoff), "a B-roll scene's clip"
    return handoff.prompt


def _passes(row: AgentFixTry, clip: ProducedItem) -> bool:
    """Whether a try passes: the judge passed it and, for door 2, its fix check didn't fail
    (a check no answer settled leaves it passing, as the judge's own)."""
    if (clip.ruling or {}).get("verdict") == "fail":
        return False
    return (row.fix_check or {}).get("verdict") != "failed"


def _told(
    fixing: AgentFix,
    row: AgentFixTry,
    *,
    changed: list[str] | None = None,
    shown: list[Image] | None = None,
) -> ToolResult:
    """What the agent is told of a filmed try: what changed, its picture and frames, the
    judge's words and, for door 2, whether the owner's change is made."""
    assert row.clip is not None
    label = f"Try {row.number}"
    pictures = list(shown or [])
    if row.picture is not None and row.new_picture and not pictures:
        pictures.append(Image(label=f"{label}, new start picture", key=row.picture.file))
    pictures += [
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
    if row.fix_check is not None:
        made = row.fix_check["verdict"]
        words.append(
            "Whether the owner's change is made: "
            + {"passed": "yes.", "failed": f"no. {row.fix_check['reason']}"}.get(
                made, "it couldn't be told."
            )
        )
    words.append("It passes." if row.passed else "It fails.")
    return ToolResult("\n".join(words), pictures)


def _fact_checked(scene: Scene) -> None:
    """Fact check the scene as the agent wrote it; refuse it when the check fails it."""
    checked = work._ask_fact_check(scene.job, [work._to_check(scene)], [], pay_once=True)
    verdict = checked.lines[0] if checked.decision == "checked" and checked.lines else None
    if verdict is None:
        raise Refused(
            f"the fact check couldn't tell whether it is true: {checked.question or checked.reason}"
        )
    if verdict.verdict != "ok":
        raise Refused(
            f"the fact check failed it: {verdict.problem} The page says: {verdict.page_says}"
        )
    scene.fact_checked = True
    scene.fact_problems = []


CANT_BE_VOICED = "the new line can't be voiced right now, so keep the scene's line as it is."


def _voiced(fixing: AgentFix, call: ToolCall) -> ProducedItem:
    """The new line, said by the ad's voice, and heard back: refused when it can't be."""
    job = fixing.step.scene.job
    voice = work.latest(job, ProducedItem.Kind.VOICE)
    step = _try_step(fixing, call, SceneStep.Kind.LINE_AUDIO, made_from=voice)
    try:
        audio = work._speak_line(step)
    except Exception as error:
        _failed(step, str(error))
        raise Refused(CANT_BE_VOICED) from error
    _done(step)
    assert audio.seconds is not None
    if audio.seconds > MOST_BROLL_SECONDS:
        raise Refused(
            f"the new line takes {audio.seconds:.1f} s to say, over the "
            f"{MOST_BROLL_SECONDS} s a clip lasts."
        )
    heard = _try_step(fixing, call, SceneStep.Kind.TRANSCRIPT, made_from=audio)
    try:
        work.transcribe_line_audio(heard)
    except Exception as error:
        # Never heard back, the line can't be cut to: as good as not voiced.
        _failed(heard, str(error))
        raise Refused(CANT_BE_VOICED) from error
    _done(heard)
    return audio


def _drawn(
    fixing: AgentFix, asked: FilmAgain, call: ToolCall, number: int, shown: list[Image]
) -> ProducedItem | str:
    """The try's new start picture, checked; or, when the check fails it, what the agent is
    told: draw it again, or, after the last redraw, that the try ends with no clip."""
    assert asked.photo is not None and asked.picture_prompt is not None
    files, rows = _pictures_sent(fixing, asked.person_shown, asked.photo)
    owners = _owner_photos(fixing.step, ReferencePhoto.Use.PICTURE)
    step = _try_step(
        fixing,
        call,
        SceneStep.Kind.STARTING_PICTURE,
        photo=fixing.step.scene.job.photos.get(position=asked.photo),
        prompt=asked.picture_prompt,
        motion_prompt=asked.video_prompt,
        way=SceneStep.Way.FROM_PICTURE,
        pictures_sent=rows,
    )
    prompt = (
        f"{reference_photos.picture_label([row.thing for row in owners])} {asked.picture_prompt}"
        if owners
        else asked.picture_prompt
    )
    # A picture drawn for this try before the worker stopped isn't paid for again.
    drawn = work._pictures_drawn(step)
    file = (
        drawn[0]
        if drawn
        else edit_picture(
            job=fixing.step.scene.job,
            purpose="make_starting_picture",
            prompt=prompt,
            pictures=files,
        )
    )
    problems = work._check_broll_picture(step, file)
    shown.append(Image(label=f"Try {number}, new start picture", key=file))
    if not problems:
        picture = work._keep_picture(step, file)
        ProducedItem.objects.filter(pk=picture.pk).update(change=f"try {number}")
        _done(step)
        picture.refresh_from_db()
        return picture
    _failed(step, "; ".join(problems))
    fixing.redraws += 1
    fixing.save(update_fields=["redraws"])
    if fixing.redraws <= MOST_REDRAWS:
        raise Refused(
            f"the picture check failed the new start picture: {' '.join(problems)} Draw it "
            f"again (redraw {fixing.redraws} of {MOST_REDRAWS}; it doesn't count as a try)."
        )
    # The last redraw failed too: the try ends with nothing filmed, and the scene as it was.
    AgentFixTry.objects.create(
        agent_fix=fixing,
        number=number,
        tool_call=call,
        scene_row=_row(fixing.step.scene),
        new_picture=True,
        picture_prompt=asked.picture_prompt,
        root_cause=asked.root_cause,
        move=asked.move,
        would_ask_owner=asked.would_ask_owner or "",
        picture_problems=[problems],
    )
    fixing.redraws = 0
    fixing.save(update_fields=["redraws"])
    return (
        f"The picture check failed the new start picture: {' '.join(problems)} That was the "
        f"last redraw: try {number} ends with no clip."
    )
