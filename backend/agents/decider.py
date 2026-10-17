"""The decider (fix redesign, decisions 3, 5, 9, 10, 13, 15-19, 21): the agent that reads the
shop owner's messages once the ad has a clip, looks at what it needs with read-only tools,
and answers once, in `decide`, with what changes in each scene. Code records that as a fix
plan (jobs.fix_plans) and does everything else: showing the plan, reading the yes, drawing,
making clips and going back. The decider never speaks in the chat.

One round is every message from the shop owner since their last yes (decision 18): within it
the decider's conversation is kept, rebuilt from its checkpoints as the loop does, so a
correction builds on the looks already made; at the next yes it starts afresh. Two rules are
kept by code, from those checkpoints (decision 3): no scene is decided before it was looked
at, and no scene goes back to a version before that version was looked at."""

import contextvars
import hashlib
import io
import json
import math
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Literal

import PIL.Image
from django.db import transaction
from django.db.models import QuerySet
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from adforge import file_store, tracing
from chat import messages
from chat.models import Attachment, Message, Session
from gateway.gateway import take_turn
from gateway.types import Happened, Image, Said, Shown, ToolUse
from jobs import clip_judge, fix_plans, work
from jobs.models import (
    FixCheck,
    FixPlan,
    FixPlanScene,
    Job,
    ProducedItem,
    ReferencePhoto,
    Scene,
)

from . import loop, run_plan
from .loop import Agent, Refused, Tool, ToolResult
from .models import ToolCall

PURPOSE = "decide_fixes"

INSTRUCTIONS = """\
You decide how to fix a short video ad from its shop owner's notes. The ad is made of \
scenes: in some a person speaks a line to camera, in others the product is shown while the \
person's voice says the line over it. Each scene has a starting picture and a clip made \
from it, and every time a scene was made again it got a new version, so it has a list of \
versions: "v1 first version", then one for each change.
You don't talk to the shop owner and you make nothing yourself. You read their messages, \
look at what you need, and answer once, with decide. The app shows your plan to the shop \
owner and does all of it once they say yes.
First, sort their newest message with sort_message, before you look at anything. It is a \
fix note when it asks for something in the ad to change, or corrects the plan you made. It \
is yes or no only when a plan is waiting for their answer and the message answers it: \
"yeah go" is yes, "wait" is not a fix. It is go ahead only when a fix check failed and \
waits for their answer, and the message says to carry on with the new version as it is. \
Anything else, such as a question about the cost, is not a fix. When it isn't a fix note, \
you are done.
For a fix note, look before you decide. You can't decide a scene you haven't looked at, or \
go back to a version you haven't looked at. Look at a scene's starting picture for what \
the picture shows, and at its clip's frames for what happens or how things move. The \
judge's findings on a clip are for notes about how things move, a product that flips to \
another side, or the action not happening; don't look at them for anything else. Look at \
each of the shop owner's photos you use.
Then decide, for each scene the notes are about, the smallest thing to make again that \
fixes it:
- a new picture, when what the picture shows is wrong: the wrong side, the wrong thing on \
screen, something that couldn't be real, a colour. "Zoom in" is a new picture, closer: the \
camera never moves. A new picture gets a new clip made from it;
- a new clip, when how things move or what happens is wrong;
- a new line, when what the scene says should change: give the new line word for word. \
A new line keeps the scene's starting picture: plan a new picture only when the shop owner \
asks for something different on screen. When they ask for both on one scene, it is a new \
picture with the new line in its line, and its change says only what must look different \
on screen, never the words to say;
- go back to version N, when they liked an earlier version better: say which number, after \
looking at it;
- no change, when the scene already shows what they ask.
In a scene where the person talks to camera, the clip only makes them speak the line from \
the starting picture: what they hold, wear or stand next to is a new picture, and an action \
while they talk, such as pointing or showing how the product works, is a can't on that \
scene, beside any new picture the rest of the note needs.
For each scene, say the change in plain words: what must be different, never how to make \
it. Say what the scene must show, not what is wrong now. The change says only what the \
shop owner asked, in their words; don't add how the scene ends or anything else they didn't \
ask for.
Some things can't be done yet. Changing how the person looks, or making a clip slower \
(every clip plays at real-time speed), is a can't on the scene the note is about, with no \
change. Changing the music, the voice or the number of scenes, using a video as an \
example, or anything that isn't about the ad is a can't of the whole plan, in its cants, \
with no scene and no look.
The shop owner can send photos of their own things. For each photo you use, name the one \
thing to take from it, in their words, such as "black toilet". For each scene that shows \
that thing, answer two questions: is it in the starting picture now, and does more of it \
show during the action? Both can be yes. When the thing is in the picture and the scene's \
plan says whether it is used during the action ("scoops gel from the jar" is yes, "holds \
the jar up" is no), you don't need the clip's frames. When it doesn't say, look at the \
frames. When the thing is in no starting picture, read each scene's plan for where it \
shows, look at those scenes' frames to confirm, and add each scene where you see it, \
saying why in its change. Found nowhere: ask the shop owner which scene. Scenes they name \
win: "only scene 2" means only scene 2. When a photo doesn't show its thing clearly enough \
to copy how it looks, say it isn't clear.
When you can't tell what they want, ask one short question instead, in the question of \
decide, in plain words."""

# How code says a can't the decider found (decision 13, the judgment can'ts), the same way
# every time, as the producer says them today: a person's look and slower on the scene's
# row, the others on the plan (FixPlan.Cant).
CANTS: dict[str, str] = {
    "person_look": "Changing how the person looks can't be done yet.",
    "music": "Changing the music can't be done yet.",
    "voice": "Changing the voice can't be done yet.",
    "number_of_scenes": "Changing the number of scenes can't be done yet.",
    "slower": "Slower can't be done: every clip plays at real-time speed.",
    "talking_action": "A person can't do an action while talking yet.",
    "example_video": "Using a video as an example can't be done yet.",
    "not_about_the_ad": "That isn't something the ad can change.",
}

# Turns the decider may end with no tool, each answered with a reminder, before it stops.
MOST_REMINDERS = 2
# The most tools the decider may call for one message (design thread, 05b: a GUESS), far
# under the limit for every agent together, so a decider going round in circles stops cheap.
# Counted before each turn, so the turn that reaches it may run a few calls past it.
MOST_TOOL_CALLS = 30
REMINDER = "Answer with a tool call: sort_message, a look, or decide. Nothing you write is shown."


class NoDecision(Exception):
    """The decider stopped without an answer: it called no tool when reminded, or hit the
    limit of tools for one message. Says why."""


@dataclass(frozen=True)
class NotAFix:
    """The newest message isn't a fix note: the producer answers it. A plan waiting for the
    shop owner's answer stays open."""


@dataclass(frozen=True)
class Planned:
    """A fix plan was recorded from this round's notes, a correction included: it replaces
    any plan that was waiting."""

    plan: FixPlan


@dataclass(frozen=True)
class Yes:
    """The newest message says yes to the plan waiting for an answer."""

    plan: FixPlan


@dataclass(frozen=True)
class No:
    """The newest message says no to the plan waiting for an answer."""

    plan: FixPlan


@dataclass(frozen=True)
class GoAhead:
    """The newest message says go ahead to the scenes whose fix check failed (flow step 9):
    each carries on with its new version as it is."""

    rows: tuple[FixPlanScene, ...]


Outcome = NotAFix | Planned | Yes | No | GoAhead


# ---------------------------------------------------------------- the round


@dataclass(frozen=True)
class _Round:
    job: Job
    said: tuple[Message, ...]

    @property
    def session(self) -> Session:
        assert self.job.session is not None, "the decider works in the job's chat"
        return self.job.session

    @property
    def started(self) -> datetime:
        return self.said[0].created_at

    def calls(self) -> QuerySet[ToolCall]:
        """Every tool the decider called this round, in order."""
        return ToolCall.objects.filter(
            session=self.session, agent=DECIDER_NAME, created_at__gte=self.started
        ).order_by("created_at", "id")

    def done(self) -> QuerySet[ToolCall]:
        """The calls this round that did what they were asked."""
        return (
            self.calls()
            .filter(finished_at__isnull=False)
            .exclude(result__startswith="Refused:")
            .exclude(result__startswith="Failed:")
        )


_round: contextvars.ContextVar[_Round] = contextvars.ContextVar("decider_round")


@contextmanager
def _in(round: _Round) -> Iterator[None]:
    token = _round.set(round)
    try:
        yield
    finally:
        _round.reset(token)


def this_round(job: Job) -> list[Message]:
    """The shop owner's messages since they last said yes to a plan, once the ad had a clip
    (decision 18): what the decider is given. Messages from before the first clip are the
    producer's, about making the ad."""
    assert job.session is not None
    first_clip = job.produced.filter(kind=ProducedItem.Kind.CLIP).order_by("created_at").first()
    if first_clip is None:
        return []
    said = job.session.messages.filter(role=Message.Role.USER, created_at__gt=first_clip.created_at)
    yes = (
        FixPlan.objects.filter(session=job.session, confirmed_by__isnull=False)
        .order_by("confirmed_by__seq")
        .last()
    )
    if yes is not None and yes.confirmed_by is not None:
        said = said.filter(seq__gt=yes.confirmed_by.seq)
    return list(said.order_by("seq").prefetch_related("attachments"))


# ---------------------------------------------------------------- versions


def _version_of(scene: Scene, number: int | None) -> work.Version:
    """Version `number` of the scene, or its current one."""
    versions = work.versions(scene)
    if not versions:
        raise Refused(f"scene {scene.number} has nothing made yet.")
    if number is None:
        return versions[-1]
    if not 1 <= number <= len(versions):
        raise Refused(f"scene {scene.number} has versions 1 to {len(versions)}, not {number}.")
    return versions[number - 1]


def _named(version: work.Version, current: int) -> str:
    return f"{version.label}{' (the current version)' if version.number == current else ''}"


# ---------------------------------------------------------------- the tools


def _scene(number: int) -> Scene:
    scene = _round.get().job.scenes.filter(number=number).first()
    if scene is None:
        raise Refused(f"the ad has no scene {number}.")
    return scene


def _owner_photo(number: int) -> Attachment:
    photos = messages.owner_photos(_round.get().session)
    if not 1 <= number <= len(photos):
        raise Refused(
            f"the shop owner has sent {len(photos)} photos, so there's no photo {number}."
        )
    return photos[number - 1]


def _sorted_newest(round: _Round) -> ToolCall | None:
    """The sorting of the newest message, once it is made. Found by the message it names, not
    by time: a sort made for an earlier message can be kept after a newer one came in."""
    return (
        round.done()
        .filter(tool=SortMessage.name, result__startswith=_sorted_as(round.said[-1]))
        .last()
    )


def _sorted_as(message: Message) -> str:
    """How a sort's result begins: with the message it sorted."""
    return f"Sorted message {message.seq} as "


def _sorted_first() -> None:
    if _sorted_newest(_round.get()) is None:
        raise Refused("sort the shop owner's newest message with sort_message first.")


class SortMessage(Tool):
    """Sort the shop owner's newest message, before anything else. fix_note: it asks for
    something in the ad to change, or corrects the plan you made. yes or no: it answers the
    plan waiting for their answer. go_ahead: it says to carry on with a new version whose
    fix check failed. not_a_fix: anything else, such as a question."""

    name: ClassVar[str] = "sort_message"

    kind: Literal["fix_note", "not_a_fix", "yes", "no", "go_ahead"]
    scenes: list[int] = Field(
        default_factory=list,
        description="For go_ahead: the scenes whose failed fix check it says to carry on. "
        "Needed when more than one waits.",
    )

    def run(self, call: ToolCall) -> str:
        round = _round.get()
        if self.kind == "go_ahead":
            _gone_ahead(round, self.scenes)
        waiting = fix_plans.open_plan(round.session)
        if self.kind in ("yes", "no") and waiting is None:
            raise Refused("no plan is waiting for the shop owner's answer.")
        if self.kind == "yes" and waiting is not None and not run_plan.runs_something(waiting):
            raise Refused(
                "the plan waiting has nothing to run, so a yes answers its question or "
                "agrees with it: sort it as fix_note, or not_a_fix."
            )
        sorted_as = f"{_sorted_as(round.said[-1])}{self.kind}."
        if self.kind == "fix_note":
            return f"{sorted_as} Look at what you need, then decide."
        return f"{sorted_as} You are done."


def _gone_ahead(round: _Round, scenes: Sequence[int]) -> None:
    """Refuse a go ahead naming `scenes` when no fix check waits, or it is unclear which
    scenes it carries on (flow step 9: go ahead continues that scene). Naming none is the
    one scene waiting."""
    held = run_plan.held_on_a_check(round.session, before=round.said[-1].seq)
    if not held:
        raise Refused("no fix check is waiting for the shop owner's go ahead.")
    waiting = sorted({row.scene.number for row in held})
    if not scenes and len(waiting) > 1:
        raise Refused(
            f"fix checks wait on {_scenes(waiting)}: name the scenes they say "
            "go ahead to. A message that also asks to change one of them is a fix_note."
        )
    if unknown := sorted(set(scenes) - set(waiting)):
        raise Refused(f"no fix check waits on {_scenes(unknown)}; they wait on {_scenes(waiting)}.")


def _scenes(numbers: Sequence[int]) -> str:
    """ "scene 2", or "scenes 2 and 3"."""
    if len(numbers) == 1:
        return f"scene {numbers[0]}"
    return f"scenes {', '.join(str(number) for number in numbers[:-1])} and {numbers[-1]}"


class LookAtPicture(Tool):
    """See a scene's current starting picture: what the scene shows now, before it moves."""

    name: ClassVar[str] = "look_at_picture"

    scene: int

    def run(self, call: ToolCall) -> ToolResult:
        _sorted_first()
        return _picture_of(_scene(self.scene), None)


class LookAtVersion(Tool):
    """See an earlier version's starting picture, by its number in the scene's version list."""

    name: ClassVar[str] = "look_at_version"

    scene: int
    version: int

    def run(self, call: ToolCall) -> ToolResult:
        _sorted_first()
        return _picture_of(_scene(self.scene), self.version)


def _picture_of(scene: Scene, number: int | None) -> ToolResult:
    version = _version_of(scene, number)
    current = work.versions(scene)[-1].number
    if version.picture is None:
        raise Refused(f"scene {scene.number}'s {version.label} has no starting picture.")
    label = f"Scene {scene.number}, starting picture, {_named(version, current)}"
    return ToolResult(text=f"{label}.", pictures=[Image(label=label, key=version.picture.file)])


class LookAtClip(Tool):
    """See a scene's clip as frames, 5 a second, each labelled with its time: how things
    move and what happens. The current version's clip, or an earlier version's by number."""

    name: ClassVar[str] = "look_at_clip"

    scene: int
    version: int | None = Field(description="The version's number, or null for the current one.")

    def run(self, call: ToolCall) -> ToolResult:
        _sorted_first()
        scene = _scene(self.scene)
        version = _version_of(scene, self.version)
        clip = version.clip
        if clip is None:
            raise Refused(f"scene {scene.number}'s {version.label} has no clip.")
        named = f"Scene {scene.number}, clip of {_named(version, work.versions(scene)[-1].number)}"
        frames = _frames(clip)
        return ToolResult(
            text=f"{named}: {clip_judge.FRAMES_PER_SECOND} frames a second, each labelled with "
            f"its time. {_judged(clip)}",
            pictures=[
                Image(label=f"{named}, {clip_judge.frame_label(each['times'])}", key=each["key"])
                for each in frames
            ],
        )


def _frames(clip: ProducedItem) -> list[dict[str, Any]]:
    """The clip's frames as the judge was shown them for movement, kept on its ruling, or
    cut now for a clip it never judged."""
    kept = ((clip.ruling or {}).get("pictures") or {}).get("frames")
    if kept:
        return kept  # type: ignore[no-any-return]
    return clip_judge.cut_pictures(clip.file, beside=0, sets=("frames",))["frames"]


def _judged(clip: ProducedItem) -> str:
    return (
        f"{_verdict(clip)} What it found is in judge_findings, for a note about how things "
        "move, a product that flips to another side, or the action not happening."
    )


def _verdict(clip: ProducedItem) -> str:
    return {
        "pass": "The judge passed this clip.",
        "fail": "The judge failed this clip.",
        "not_checked": "The judge couldn't check this clip.",
    }.get((clip.ruling or {}).get("verdict") or "", "The judge didn't judge this clip.")


class JudgeFindings(Tool):
    """What the clip judge found wrong in a scene's clip: only for notes about how things
    move, a product that flips to another side, or the action not happening. The judge can
    be wrong, so see the frames for yourself too."""

    name: ClassVar[str] = "judge_findings"

    scene: int
    version: int | None = Field(description="The version's number, or null for the current one.")

    def run(self, call: ToolCall) -> str:
        _sorted_first()
        scene = _scene(self.scene)
        version = _version_of(scene, self.version)
        if version.clip is None:
            raise Refused(f"scene {scene.number}'s {version.label} has no clip.")
        ruling = version.clip.ruling or {}
        named = f"Scene {scene.number}, clip of {version.label}"
        found = clip_judge.still_failing(ruling) if ruling.get("verdict") == "fail" else []
        let_go = [each["problem"] for each in ruling.get("let_go", [])]
        return " ".join(
            [
                f"{named}: {_verdict(version.clip)}",
                *(f"It found: {problem}" for problem in dict.fromkeys(found)),
                *(f"It let go, as back to normal soon: {problem}" for problem in let_go),
            ]
        )


class LookAtOwnerPhoto(Tool):
    """See a photo the shop owner sent, by its number in the chat ("owner photo 2")."""

    name: ClassVar[str] = "look_at_owner_photo"

    photo: int

    def run(self, call: ToolCall) -> ToolResult:
        _sorted_first()
        label = f"Owner photo {self.photo}"
        return ToolResult(
            text=f"{label}.", pictures=[Image(label=label, key=_owner_photo(self.photo).file)]
        )


class PhotoThing(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    photo: int = Field(description="The photo's number in the chat.")
    thing: str = Field(
        description='The one thing to take from it, in their words, such as "black toilet".'
    )
    clear: bool = Field(description="Whether it shows the thing clearly enough to copy its look.")

    # A validator rather than max_length, which OpenAI's strict schema doesn't accept.
    @field_validator("thing")
    @classmethod
    def _a_thing_that_can_be_kept(cls, thing: str) -> str:
        longest = ReferencePhoto._meta.get_field("thing").max_length
        assert longest is not None
        if len(thing) > longest:
            raise ValueError(f"the thing is over {longest} characters, too long to be kept")
        return thing


class PhotoUse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    photo: int
    in_picture: bool = Field(
        description="Whether the thing is in the scene's starting picture now."
    )
    during_action: bool = Field(description="Whether more of it shows during the scene's action.")


class SceneDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scene: int
    action: Literal["new_picture", "new_clip", "new_line", "go_back", "no_change"]
    change: str = Field(
        description="What must be different, in plain words, never how to make it. For a new "
        "picture with a new line, only what must look different, never the words to say. Blank "
        "for no change."
    )
    line: str = Field(
        description="The new line word for word, for new_line, or for new_picture when the "
        "scene must also say something new. Blank otherwise."
    )
    go_back_to: int | None = Field(description="The version's number, for go_back. Null otherwise.")
    cant: Literal["", "person_look", "slower", "talking_action"] = Field(
        description="What the note asks of this scene that can't be done yet. Blank for none."
    )
    photos: list[PhotoUse] = Field(
        description="The shop owner's photos whose thing the scene shows."
    )

    @model_validator(mode="after")
    def _whole(self) -> SceneDecision:
        if self.action == "new_line" and not self.line.strip():
            raise ValueError(f"scene {self.scene} is a new line, so it needs the new line")
        if self.action not in ("new_line", "new_picture") and self.line.strip():
            raise ValueError(
                f"scene {self.scene} has a line, but only a new line or a new picture takes one"
            )
        if (self.action == "go_back") != (self.go_back_to is not None):
            raise ValueError(f"scene {self.scene}: go_back, and only go_back, takes go_back_to")
        if self.action not in ("no_change", "go_back") and not self.change.strip():
            raise ValueError(f"scene {self.scene} needs the change in plain words")
        return self


RECORDED = "Recorded. The app shows your plan to the shop owner. You are done."


class Decide(Tool):
    """Answer with what changes in each scene the notes are about, once you have looked at
    each of them. With the shop owner's photos you use, each with its one thing, and at most
    one question for them, blank for none."""

    name: ClassVar[str] = "decide"

    scenes: list[SceneDecision]
    photos: list[PhotoThing]
    cants: list[
        Literal["music", "voice", "number_of_scenes", "example_video", "not_about_the_ad"]
    ] = Field(description="What the notes ask that can't be done yet and isn't about one scene.")
    question: str = Field(description="One short question for the shop owner. Blank for none.")

    def run(self, call: ToolCall) -> str:
        _sorted_first()
        round = _round.get()
        sorting = _sorted_newest(round)
        assert sorting is not None, "_sorted_first refuses an unsorted message"
        if sorting.arguments["kind"] != "fix_note":
            raise Refused(
                "the shop owner's newest message is sorted as "
                f"{sorting.arguments['kind']}, so there is nothing to decide."
            )
        if FixPlan.objects.filter(session=round.session, created_at__gte=call.created_at).exists():
            # A worker stopped after the plan was recorded, before this call was kept as done.
            return RECORDED
        things = {each.photo: each.thing for each in self.photos}
        clear = {each.photo: each.clear for each in self.photos}
        rows = []
        seen: set[int] = set()
        for decided in self.scenes:
            if decided.scene in seen:
                raise Refused(f"scene {decided.scene} is decided twice.")
            seen.add(decided.scene)
            scene = _scene(decided.scene)
            self._looked(round, decided)
            for used in decided.photos:
                if used.photo not in things:
                    raise Refused(
                        f"scene {decided.scene} uses photo {used.photo}, which isn't in photos "
                        "with its thing."
                    )
            rows.append(
                FixPlanScene(
                    scene=scene,
                    action=decided.action,
                    change=decided.change,
                    line=decided.line,
                    go_back_to=decided.go_back_to,
                    cant=CANTS[decided.cant] if decided.cant else "",
                    photos=[
                        {
                            "photo": used.photo,
                            "file": _owner_photo(used.photo).file,
                            "thing": things[used.photo],
                            "in_picture": used.in_picture,
                            "during_action": used.during_action,
                            "clear": clear[used.photo],
                        }
                        for used in decided.photos
                    ],
                )
            )
        for photo in things:
            _owner_photo(photo)
            if not _looked_at(round, LookAtOwnerPhoto.name, photo=photo):
                raise Refused(f"look at owner photo {photo} first.")
        if not rows and not self.cants and not self.question.strip():
            raise Refused(
                "decide at least one scene, give a can't, or ask the shop owner one question."
            )
        # Code checks the rule can'ts and sets each scene's estimate before the plan is shown.
        run_plan.check_and_propose(
            round.job,
            [{"seq": message.seq, "text": message.text} for message in round.said],
            rows,
            question=self.question.strip(),
            cants=[{"kind": kind, "text": CANTS[kind]} for kind in dict.fromkeys(self.cants)],
        )
        return RECORDED

    @staticmethod
    def _looked(round: _Round, decided: SceneDecision) -> None:
        """Refuse a scene decided without a look at it, a go-back without a look at its
        version, and a photo's thing only during the action without a look at the clip."""
        # The judge's findings are its words, not a look: it can be wrong (decision 16).
        looks = (LookAtPicture.name, LookAtVersion.name, LookAtClip.name)
        if not any(_looked_at(round, tool, scene=decided.scene) for tool in looks):
            raise Refused(f"look at scene {decided.scene} first.")
        if decided.go_back_to is not None and not any(
            _looked_at(round, tool, scene=decided.scene, version=decided.go_back_to)
            for tool in (LookAtVersion.name, LookAtClip.name)
        ):
            raise Refused(f"look at version {decided.go_back_to} of scene {decided.scene} first.")
        if any(
            used.during_action and not used.in_picture for used in decided.photos
        ) and not _looked_at(round, LookAtClip.name, scene=decided.scene):
            raise Refused(
                f"look at scene {decided.scene}'s clip first: you say the photo's thing shows "
                "there only during the action."
            )


def _looked_at(round: _Round, tool: str, **arguments: int) -> bool:
    """Whether the decider called `tool` this round with these arguments, and it did."""
    return any(
        all(call.arguments.get(name) == value for name, value in arguments.items())
        for call in round.done().filter(tool=tool)
    )


DECIDER_NAME = "decider"

DECIDER = Agent(
    name=DECIDER_NAME,
    purpose=PURPOSE,
    instructions=INSTRUCTIONS,
    tools=[
        SortMessage,
        LookAtPicture,
        LookAtVersion,
        LookAtClip,
        JudgeFindings,
        LookAtOwnerPhoto,
        Decide,
    ],
)


# ---------------------------------------------------------------- reading a round


def read(job: Job, said: Sequence[Message]) -> Outcome:
    """Have the decider read this round's messages from the shop owner (`said`, oldest
    first, the newest the one to answer) and answer: not a fix, a plan recorded, or yes or
    no to the plan waiting. Raises NoDecision when it stops without an answer. A worker that
    stopped halfway is replaced by one that calls this again and carries on."""
    assert said, "the decider reads at least one message"
    round = _Round(job, tuple(said))
    session = round.session
    with _in(round):
        for call in round.calls().filter(finished_at__isnull=True):
            loop.settle(DECIDER, call)
        reminders: list[Happened] = []
        reminded = 0
        while True:
            outcome = _outcome(round)
            if outcome is not None:
                return outcome
            if loop.over_the_limit(session):
                raise NoDecision("it hit the limit of tools for one message")
            if round.calls().filter(created_at__gt=round.said[-1].created_at).count() >= (
                MOST_TOOL_CALLS
            ):
                raise NoDecision("it hit the decider's limit of tools for one message")
            with tracing.agent_turn(DECIDER_NAME):
                turn = take_turn(
                    session=session,
                    purpose=PURPOSE,
                    instructions=INSTRUCTIONS,
                    conversation=[*_conversation(round), *reminders],
                    tools=[tool.spec() for tool in DECIDER.tools],
                    since=round.started,
                )
                if not turn.calls:
                    if reminded == MOST_REMINDERS:
                        raise NoDecision("it answered without a tool when reminded")
                    reminded += 1
                    reminders += [
                        Said(by="agent", text=turn.says or "(nothing)"),
                        Said(by="user", text=REMINDER),
                    ]
                    continue
                reminders = []
                with transaction.atomic():
                    calls = [
                        ToolCall.objects.create(
                            session=session,
                            job=job,
                            agent=DECIDER_NAME,
                            tool=asked.tool,
                            call_id=asked.call_id,
                            arguments=asked.arguments,
                        )
                        for asked in turn.calls
                    ]
                for call in calls:
                    loop.settle(DECIDER, call)


def _outcome(round: _Round) -> Outcome | None:
    """The decider's answer to the newest message, once it has given one."""
    sorting = _sorted_newest(round)
    if sorting is None:
        return None
    kind = sorting.arguments["kind"]
    if kind == "not_a_fix":
        return NotAFix()
    if kind in ("yes", "no"):
        plan = fix_plans.open_plan(round.session)
        assert plan is not None, "sort_message refuses yes or no with no plan waiting"
        return Yes(plan) if kind == "yes" else No(plan)
    if kind == "go_ahead":
        # The scenes sort_message checked when it was called.
        scenes = sorting.arguments.get("scenes") or []
        held = run_plan.held_on_a_check(round.session, before=round.said[-1].seq)
        return GoAhead(tuple(row for row in held if not scenes or row.scene.number in scenes))
    decided = round.done().filter(tool=Decide.name, created_at__gt=sorting.created_at).last()
    if decided is None:
        return None
    plan = (
        FixPlan.objects.filter(session=round.session, created_at__gte=decided.created_at)
        .order_by("created_at", "id")
        .first()
    )
    assert plan is not None, "decide records a plan"
    return Planned(plan)


def _conversation(round: _Round) -> list[Happened]:
    """What the decider is given: the ad as it stands, then this round's messages from the
    shop owner and its own tool calls, in the order they happened. Every picture is sent
    again on each turn, so only the ones it needs go (`_shown`)."""
    calls = list(round.calls())
    shown = _shown(calls, sorted_yet=_sorted_newest(round) is not None)
    happened: list[tuple[datetime, int, Happened]] = [
        (message.created_at, 0, Said(by="user", text=messages.as_read(message, numbered=True)))
        for message in round.said
    ] + [
        (
            call.created_at,
            1,
            ToolUse(
                call_id=call.call_id,
                tool=call.tool,
                arguments=call.arguments,
                result=call.result + shown[call.pk][1],
                pictures=[Shown(**picture) for picture in shown[call.pk][0]],
            ),
        )
        for call in calls
    ]
    return [
        Said(by="user", text=_the_ad(round)),
        *(each for _, _, each in sorted(happened, key=lambda each: (each[0], each[1]))),
    ]


# The most pictures one turn may carry: what the model takes in one ask.
MOST_PICTURES = clip_judge.MOST_PICTURES


def _shown(
    calls: list[ToolCall], *, sorted_yet: bool
) -> dict[int, tuple[list[dict[str, str]], str]]:
    """Per call, the pictures this turn shows of it and what its result says of those it
    doesn't. None before the newest message is sorted: sorting needs only the words. Then each
    look's pictures once, with its newest call, fitted together into MOST_PICTURES (`_fitted`);
    past it even so, the newest looks first."""
    shown: dict[int, tuple[list[dict[str, str]], str]] = {call.pk: ([], "") for call in calls}
    if not sorted_yet:
        return shown
    looks: list[ToolCall] = []
    seen: set[str] = set()
    for call in reversed(calls):
        if not call.pictures:
            continue
        look = f"{call.tool} {json.dumps(call.arguments, sort_keys=True)}"
        if look in seen:
            shown[call.pk] = ([], " (Its pictures are shown with the same look, later.)")
        else:
            looks.append(call)
        seen.add(look)
    fitted = _fitted(looks)
    room = MOST_PICTURES
    for call in looks:
        if len(fitted[call.pk]) > room:
            shown[call.pk] = ([], " (Its pictures aren't shown any more: look again to see them.)")
        else:
            shown[call.pk] = (fitted[call.pk], "")
            room -= len(fitted[call.pk])
    return shown


def _fitted(looks: list[ToolCall]) -> dict[int, list[dict[str, str]]]:
    """Each look's pictures, with every clip's frames joined side by side, as few to a
    picture as lets every look fit in MOST_PICTURES: the clip judge's way of fitting frames
    (`clip_judge.cut_pictures`), so each frame is kept at FRAMES_PER_SECOND (decision 10) and
    a note about two clips sees both at once."""
    clips = [look for look in looks if look.tool == LookAtClip.name]
    others = sum(len(look.pictures) for look in looks if look.tool != LookAtClip.name)
    together = 1
    while together < max((len(clip.pictures) for clip in clips), default=1) and (
        others + sum(math.ceil(len(clip.pictures) / together) for clip in clips) > MOST_PICTURES
    ):
        together += 1
    return {
        look.pk: [
            _joined_frames(look.pictures[i : i + together])
            for i in range(0, len(look.pictures), together)
        ]
        if look.tool == LookAtClip.name
        else look.pictures
        for look in looks
    }


def _opens(data: bytes) -> bool:
    """Whether `data` is a whole picture, not one a stopped worker left half-saved."""
    try:
        PIL.Image.open(io.BytesIO(data)).load()
    except OSError, PIL.Image.DecompressionBombError:
        return False
    return True


# A frame's label, as `LookAtClip` gives it: the clip, then the time of each frame in it.
_FRAME_LABEL = re.compile(r"^(?P<clip>.*), Frames? at (?P<times>.+?)(?:, left to right)?$")


def _joined_frames(frames: list[dict[str, str]]) -> dict[str, str]:
    """`frames` of one clip as one picture, joined left to right, labelled with the time of
    each. Kept under a name made from the frames, so a later turn, or a restart, shows the
    same picture and a paid turn is replayed for free."""
    if len(frames) == 1:
        return frames[0]
    labels = [_FRAME_LABEL.fullmatch(frame["label"]) for frame in frames]
    assert all(labels), "a look at a clip labels each frame with its times"
    times = [
        float(time.removesuffix(" s"))
        for label in labels
        for time in label["times"].split(", ")  # type: ignore[index]
    ]
    keys = [frame["key"] for frame in frames]
    key = file_store.save_once(
        f"decider_frames_{hashlib.sha256(chr(10).join(keys).encode()).hexdigest()[:32]}.jpg",
        lambda: clip_judge.joined([PIL.Image.open(io.BytesIO(file_store.read(k))) for k in keys]),
        whole=_opens,
    )
    return {"label": f"{labels[0]['clip']}, {clip_judge.frame_label(times)}", "key": key}  # type: ignore[index]


def _the_ad(round: _Round) -> str:
    """The ad as it stands, scene by scene, written by code for the decider."""
    job = round.job
    scenes = "\n\n".join(_scene_facts(scene) for scene in job.scenes.order_by("number"))
    sent = _photos_sent(round)
    plan = fix_plans.open_plan(round.session)
    return "\n\n".join(
        [
            "From the app, not the shop owner. The ad as it stands, scene by scene:",
            scenes,
            f"Photos the shop owner sent this round: {sent}."
            if sent
            else "The shop owner sent no photos this round.",
            "Your last plan is waiting for the shop owner's answer."
            if plan is not None
            else "No plan is waiting for the shop owner's answer.",
            *_held(round),
            "Their messages this round follow, oldest first.",
        ]
    )


def _held(round: _Round) -> list[str]:
    """Each fix check that failed and waits for the shop owner's answer, in a sentence."""
    told = []
    for row in run_plan.held_on_a_check(round.session, before=round.said[-1].seq):
        check = (
            FixCheck.objects.filter(step__plan_scene=row, verdict=FixCheck.Verdict.FAILED)
            .select_related("old", "new")
            .last()
        )
        assert check is not None, "a row held on a check has a failed check"
        told.append(
            f"Scene {row.scene.number}'s fix check failed on its new {check.what}: "
            f"{check.reason} The shop owner was shown the old {check.what} and the new one, and "
            "asked to say go ahead with the new one or what to change."
        )
    return told


def _scene_facts(scene: Scene) -> str:
    versions = work.versions(scene)
    current = versions[-1] if versions else None
    if not scene.shows:
        facts = [f"Scene {scene.number}: the person talks to camera.", f'Line: "{scene.line}"']
    else:
        kind = ", ".join(each for each in [scene.broll_kind, scene.person_shown] if each)
        facts = [
            f"Scene {scene.number}: shows the product{f' ({kind})' if kind else ''}.",
            f'Line: "{scene.line}"',
            f"Shows: {scene.shows}",
            *([f"How the product is used: {scene.usage}"] if scene.usage else []),
            *([f"What it ends on: {scene.result}"] if scene.result else []),
            *(
                [
                    "What it needs: "
                    + "; ".join(
                        f"{need['what']} (product photos {', '.join(map(str, need['photos']))})"
                        for need in scene.needs
                    )
                ]
                if scene.needs
                else []
            ),
        ]
        prompt = _video_prompt(current.clip) if current and current.clip else ""
        if prompt:
            facts.append(f"The current clip's video prompt: {prompt}")
    in_use = scene.reference_photos.filter(dropped_by__isnull=True).values_list("thing", flat=True)
    if in_use:
        facts.append(f"The shop owner's photos it uses: {', '.join(in_use)}")
    if current is not None:
        facts.append(
            "Versions: " + "; ".join(_named(version, current.number) for version in versions)
        )
    return "\n".join(facts)


def _video_prompt(clip: ProducedItem) -> str:
    return clip.motion_prompt or (clip.step.motion_prompt if clip.step else "")


def _photos_sent(round: _Round) -> str:
    """The numbers of the photos the shop owner sent this round, as the chat numbers them."""
    ids = {attachment.pk for message in round.said for attachment in message.attachments.all()}
    numbers = [
        str(number)
        for number, photo in enumerate(messages.owner_photos(round.session), 1)
        if photo.pk in ids
    ]
    return ", ".join(f"owner photo {number}" for number in numbers)
