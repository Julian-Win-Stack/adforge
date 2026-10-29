"""The fixing agent: one agent owns a failed B-roll scene until it is fixed or honestly given
up (handoffs/fixing-agent/spec.md). Bad means the clip judge failed the scene's clip (door 1)
or the shop owner sent a note about it (door 2). The agent sees the plan, the page, the shop
photos, the place, the failed clip's frames and what the judge or the owner said; it decides
what to change (the action, the result, the kind, the face, the framing, the start picture,
both prompts, the line), films again with the app's own steps, and reads the judge's answer.
It repeats until a try passes, or it calls give_up, or it has filmed the tries cap, when
code ends it; it is never told the cap. Every try is a version the shop owner can see and
pick.

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
from pydantic import Field

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
from jobs import auto_fix, clip_judge, picture_check, reference_photos, scenes, work
from jobs.models import AgentFix, AgentFixTry, ProducedItem, ReferencePhoto, Scene, SceneStep

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

INSTRUCTIONS = """\
You fix one B-roll scene of a short vertical video ad. In a B-roll scene the presenter's \
voice says the scene's line while the clip shows the product. The scene's clip failed: a \
judge watched it and found faults, or the shop owner failed it. You own this scene. You may \
change, for this scene only: what it shows (the action), what it ends on (the result), its \
B-roll kind, whether the presenter's face is in it, the camera framing, the start picture \
(you write its prompt from scratch), the video prompt (from scratch), and the line. You may \
film a different step of what the line says, when that step films better. The clip lasts the \
line's audio in whole seconds, at least 5 and at most 15, so a shorter or longer line changes \
the clip's length. You never change another scene, and never the place picture: every B-roll \
scene of the ad happens in that place (you may frame it closer or from another side, but it \
is the same place, with the same things in it).

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
- Never ask the shop owner anything. If you would need them (for example to know how the \
product is really used), write it in would_ask_owner and carry on as best you can.

How you work: you are shown the scene, the plan, the page, the start picture, the failed \
clip's frames (5 a second) and what the judge found. Look at anything else you need. Then \
call film_again with the whole scene as it should be, or give_up. After each \
film_again you see the new start picture, the new clip's frames and the judge's verdict. On \
a fail, find what caused it (the plan, the picture, or what the video model can't do) and \
change that cause, not only the words. Try to film it first: code refuses give_up until at \
least one new clip was made. When, after trying, you judge the scene can't be filmed well, \
call give_up with your reason: {gave_up} The judge can be wrong, but only a clip the judge \
passes passes.

The app's own prompt rules follow, for writing the two prompts. They hold for you too, with \
two exceptions: you may write the video prompt second by second, with timings, when that \
helps; and a rule about following a producer's note does not apply.
"""


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
    told = INSTRUCTIONS.replace("{gave_up}", GAVE_UP[AgentFix.Door(door)])
    return f"{told}\n{scenes.BROLL_SHARED_RULES}\n\n{kinds}"


def _tools(door: str) -> list[ToolSpec]:
    """The agent's tools, give_up described as giving up does at `door`."""
    specs = [tool.spec() for tool in TOOLS]
    if door != AgentFix.Door.OWNER:
        return specs
    return [
        replace(spec, description=GIVE_UP_FOR_A_NOTE) if spec.name == GiveUp.name else spec
        for spec in specs
    ]


# What a fixing the tries cap ended says, as its give-up reason, for the shop owner and the
# producer: code ended it, never the agent.
ENDED_BY_CAP = (
    "The fixing agent filmed as many tries as it may at this scene, and none of them passed."
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


@dataclass(frozen=True)
class Outcome:
    """How the agent's work on a scene ended: the passing try's clip, or why it gave up."""

    passed: ProducedItem | None
    tries: list[Try]
    gave_up: str | None
    cost_usd: Decimal
    # Whether code ended the fix at the tries cap, rather than the agent giving up.
    ended_by_cap: bool = False


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
        why=row.why,
        would_ask_owner=row.would_ask_owner,
        cost_usd=row.tool_call.cost_usd(),
        picked=fixing.picked == row.number,
    )


def _started(step: SceneStep, door: str, failure: Failure, goal: str | None, cap: int) -> AgentFix:
    """The step's AgentFix: started now with the scene as it stands, or as it was started."""
    with transaction.atomic():
        found = AgentFix.objects.select_for_update().filter(step=step).first()
        if found is not None:
            return found
        # As it stands now: the step's own copy may be from before its clip was made.
        scene = Scene.objects.get(pk=step.scene_id)
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
        _settle(fixing, call)
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
            if _ended(fixing):
                call.result = "Refused: the scene's fixing has ended. Nothing was done."
                call.finished_at = timezone.now()
                call.save(update_fields=["result", "finished_at"])
            else:
                _settle(fixing, call)
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


def _settle(fixing: AgentFix, call: ToolCall) -> None:
    """Run the call, as every agent's. The agent is never told how many tries it has or has
    left, so that it never quits early (spec #210, point 29)."""
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


def _put_back(scene: Scene, row: dict[str, Any]) -> None:
    Scene.objects.filter(pk=scene.pk).update(**row)
    scene.refresh_from_db()


def _fit(conversation: list[Happened]) -> list[Happened]:
    """Keep the newest pictures under MOST_PICTURES; older ones are dropped with a note."""
    room = MOST_PICTURES
    kept: list[Happened] = []
    for each in reversed(conversation):
        if isinstance(each, ToolUse) and each.pictures:
            if len(each.pictures) <= room:
                room -= len(each.pictures)
            else:
                each = each.model_copy(
                    update={
                        "pictures": [],
                        "result": f"{each.result} (Its pictures aren't shown any more: look "
                        "again to see them.)",
                    }
                )
        kept.append(each)
    return list(reversed(kept))


REMINDER = "Answer with a tool call: a look, film_again or give_up."


def _conversation(fixing: AgentFix) -> list[Happened]:
    """What the agent is given: the scene and what failed, try 0 shown, then each turn's
    words and tools in the order they happened."""
    first = fixing.first_clip
    assert first is not None, "the fixing started with try 0"
    shown = [Shown(label="Try 0, start picture", key=first.picture.file)] if first.picture else []
    happened: list[Happened] = [
        Said(by="user", text=_intro(fixing)),
        ToolUse(
            call_id="try-0",
            tool=LookAtTry.name,
            arguments={"number": 0},
            result="Try 0: the scene as it failed in the ad. Its start picture, then its "
            "clip's frames.",
            pictures=[*shown, *_frames(first, "Try 0, clip")],
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
    step = fixing.step
    scene = step.scene
    job = scene.job
    first = fixing.first_clip
    assert first is not None and first.made_from is not None
    plan = "\n".join(_scene_facts(each) for each in job.scenes.order_by("number"))
    colour = [p.position for p in job.photos.filter(shows_product_colour=True)]
    every = [p.position for p in job.photos.all()]
    planned = first.step.picture_step if first.step is not None else None
    seconds = first.made_from.seconds or 0
    clip_seconds = work._broll_clip_seconds(seconds)
    during = [row.thing for row in _owner_photos(step, ReferencePhoto.Use.CLIP)]
    opener = (
        f"{reference_photos.reference_mode_label(during)} "
        f"{scenes.broll_video_prompt('', clip_seconds).rstrip()}"
    )
    numbering = "; ".join(
        f"Image {row['image']} = {row['job']}"
        for row in _pictures_sent(fixing, scene.person_shown, (colour or every)[0])[1]
    )
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
            f"The whole plan, scene by scene:\n{plan}",
            f"You fix scene {scene.number}. Its start picture's prompt was:\n"
            f"{planned.prompt if planned else ''}",
            f"Its video prompt was:\n{work.motion_of(first)}",
            f"Its line takes {seconds:.2f} s to say, so its clip is {clip_seconds} s.",
            f"Shop photos, any of which may be Image 1: {every}; those showing the product in "
            f"the ad's colour, {job.product_colour}: {colour}. For a new start picture, pick "
            "Image 1 from those in the ad's colour; only when none suits, or there are none, "
            "take another where the product is clearly seen. The product's look and colour come "
            f"only from that photo: never change them or make one up. {scenes.SEVERAL_ITEMS}",
            "A new start picture is drawn from these pictures, in this order: "
            f'{numbering}. That order is for the face setting the scene has now: a "has face" '
            'scene sends the presenter\'s portrait just before the place, a "no face" scene '
            "leaves it out.",
            f'Code starts the video prompt with: "{opener}" then your words.',
            failed,
            f"The page's words:\n{job.page_text}",
        ]
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
        lines += [f"- It found: {fault}" for fault in dict.fromkeys(auto_fix.faults(ruling))]
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


def _joined(keys: list[str]) -> Callable[[], bytes]:
    return lambda: clip_judge.joined([PIL.Image.open(io.BytesIO(file_store.read(k))) for k in keys])


def _frames(clip: ProducedItem, label: str) -> list[Shown]:
    """The clip's frames as the judge saw them for movement, 5 a second, joined 4 to a
    picture so a turn stays under MOST_PICTURES."""
    kept = ((clip.ruling or {}).get("pictures") or {}).get("frames")
    if not kept:
        kept = clip_judge.cut_pictures(clip.file, beside=0, sets=("frames",))["frames"]
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
    """See the ad's place picture: the place every B-roll scene happens in."""

    name: ClassVar[str] = "look_at_place"

    def run(self, call: ToolCall) -> ToolResult:
        place = work.latest(_fixing_of(call).step.scene.job, ProducedItem.Kind.BEFORE_PICTURE)
        if place is None:
            raise Refused("the ad has no place picture.")
        return ToolResult("The place picture.", [Image(label="The place picture", key=place.file)])


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
    """See a try again: its start picture, its clip's frames and the judge's words. Try 0 is
    the scene as it failed in the ad."""

    name: ClassVar[str] = "look_at_try"
    number: int

    def run(self, call: ToolCall) -> ToolResult:
        fixing = _fixing_of(call)
        if self.number == 0:
            picture, clip, ruling = (
                fixing.first_clip.picture if fixing.first_clip else None,
                fixing.first_clip,
                fixing.ruling,
            )
        else:
            row = fixing.tries.filter(number=self.number).first()
            if row is None:
                raise Refused(f"tries so far: 0 to {fixing.tries.count()}.")
            picture, clip = row.picture, row.clip
            ruling = clip.ruling if clip is not None else None
        label = f"Try {self.number}"
        shown = [Image(label=f"{label}, start picture", key=picture.file)] if picture else []
        if clip is not None:
            shown += [
                Image(label=each.label, key=each.key) for each in _frames(clip, f"{label}, clip")
            ]
        return ToolResult(f"{label}. {judge_words(ruling)}", shown)


class FilmAgain(Tool):
    """Film this scene again as you write it here: code saves your scene row, draws a new
    start picture when you ask for one and checks it, makes the clip and has the judge watch
    it. Write the whole scene as it should be, every field."""

    name: ClassVar[str] = "film_again"
    why: str = Field(
        description="In plain words: what went wrong and what you change, and why that fixes it."
    )
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
        "the clip's length and the still camera before it."
    )
    would_ask_owner: str | None = Field(
        description="What you would ask the shop owner, if anything. Nobody is asked."
    )

    def run(self, call: ToolCall) -> ToolResult:
        return film_again(_fixing_of(call), self, call)


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


TOOLS: list[type[Tool]] = [
    LookAtShopPhoto,
    LookAtPlace,
    LookAtPresenter,
    LookAtTry,
    FilmAgain,
    GiveUp,
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
    last. Gives the files and the pictures_sent rows."""
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
    found = sorted({match.group(0).lower() for match in BANNED.finditer(asked.video_prompt)})
    if found:
        raise Refused(f"the video prompt has bans ({', '.join(found)}). Say what happens instead.")
    if asked.new_picture and not (asked.photo and asked.picture_prompt):
        raise Refused("a new picture needs photo and picture_prompt.")
    every = list(scene.job.photos.values_list("position", flat=True))
    if asked.new_picture and asked.photo not in every:
        raise Refused(f"Image 1 must be one of the shop photos: {every}.")
    if not asked.new_picture and asked.person_shown != scene.person_shown:
        raise Refused("showing or hiding the face changes the picture: draw a new one.")
    if asked.broll_kind != "does a job" and asked.result.strip():
        raise Refused('only a "does a job" scene has a result.')


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
            video_prompt=_as_sent(step, picture, audio, asked.video_prompt),
            why=asked.why,
            would_ask_owner=asked.would_ask_owner or "",
        )
    assert row.step is not None
    step = row.step
    try:
        clip = step.produced.filter(kind=ProducedItem.Kind.CLIP).first() or work._make_clip(
            step, motion=asked.video_prompt
        )
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
        why=asked.why,
        would_ask_owner=asked.would_ask_owner or "",
        picture_problems=[problems],
    )
    fixing.redraws = 0
    fixing.save(update_fields=["redraws"])
    return (
        f"The picture check failed the new start picture: {' '.join(problems)} That was the "
        f"last redraw: try {number} ends with no clip."
    )
