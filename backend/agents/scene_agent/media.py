"""Conversation and media readers, including historical clip records."""

import io
import json
import uuid
from collections.abc import Callable
from typing import Any

import PIL.Image

from adforge import file_store
from agents.loop import Refused
from agents.scene_forms import HOOK_PART, LAST_PART
from chat.models import Message
from gateway.types import (
    Happened,
    Image,
    Said,
    Shown,
    ToolUse,
)
from jobs import (
    auto_fix,
    clip_judge,
    library,
    library_items,
    library_sheets,
    notices,
    scene_references,
)
from jobs.library import Moment
from jobs.models import (
    AgentFix,
    Job,
    ProducedItem,
    ProductVideo,
    Scene,
)
from jobs.picture_rules import HOW_TO_METHOD_ONLY
from jobs.plan_rules import STATE_LOOKS, STYLE_LOOKS
from jobs.sent_labels import PIECE_LABEL

REMINDER = "Answer with a tool call: a look, a move or give_up."


NOT_SHOWN = "(Its pictures aren't shown any more: look again to see them.)"


MOST_PICTURES = 50


SHOWN_LATER = "(Its pictures are shown where you opened it again, later.)"


LEFT_OUT_FOR = {
    tier: f"past the video model's {library.VIDEO_API_MOST_PICTURES} pictures, {what}"
    for tier, what in {
        1: "though it was the front look photo",
        2: "though it was a pick for the scene",
        3: "though it was the drawn picture",
        4: "another side photo",
    }.items()
}


DRAWN = {
    "person_in_room": "the drawn picture of the presenter in the ad's room",
    "room": "the drawn picture of the ad's room",
}


LIBRARY_SHARE = 0.5


def _said(said: list[dict[str, Any]], at: int | None) -> list[Happened]:
    """What the agent said before its tool call number `at` (None: after its last), with a
    reminder after a turn that called no tool."""
    happened: list[Happened] = []
    for each in [each for each in said if at is None or each["at"] == at]:
        said.remove(each)
        # Code's word to the agent, as when the producer carries a paused fix on, is the
        # user's; the rest is what the agent said.
        happened.append(Said(by=each.get("by", "agent"), text=each["text"] or "(nothing)"))
        if each["no_call"]:
            happened.append(Said(by="user", text=REMINDER))
    return happened


def _fit(
    conversation: list[Happened], *, reserved: int = 0, library_reserved: int = 0
) -> list[Happened]:
    """Fit pictures around the shared images reserved by the caller.

    Keep the newest visual response first, then at most the library's share, each
    look shown once on its newest copy. Older pictures fill the remaining room or
    are replaced by NOT_SHOWN; a large newest response may borrow a library slot.
    """
    room = MOST_PICTURES - reserved
    # The newest visual response stays in view, including a fresh draw after a film.
    newest = next(
        (
            each
            for each in reversed(conversation)
            if isinstance(each, ToolUse) and each.pictures and each.tool != "look_at_library"
        ),
        None,
    )
    if newest is not None and len(newest.pictures) <= room:
        room -= len(newest.pictures)
    else:
        newest = None
    library_room = max(0, min(_library_room() - library_reserved, room))
    seen: set[tuple[str, ...]] = set()
    kept: list[Happened] = []
    for each in reversed(conversation):
        if isinstance(each, ToolUse) and each.pictures and each.tool == "look_at_library":
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
        if (
            isinstance(each, ToolUse)
            and each.pictures
            and each.tool != "look_at_library"
            and each is not newest
        ):
            if len(each.pictures) <= room:
                room -= len(each.pictures)
            else:
                each = each.model_copy(
                    update={"pictures": [], "result": f"{each.result} {NOT_SHOWN}"}
                )
        fitted.append(each)
    return list(reversed(fitted))


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


def the_product(job: Job) -> str:
    """The one product every scene draws, as the planner fixed it: its photos and its real
    size, told at the top of every scene agent's context. Empty for a job planned before the
    plan gave them."""
    if not job.product_photos and not job.real_size:
        return ""
    words = ["The product, the same in every scene of the ad, as the planner fixed it:"]
    if job.product_photos:
        words.append(
            f"its photos are {items(job.product_photos)}. Draw the product from these photos: "
            "every starting picture picks at least one of them."
        )
    if job.real_size:
        words.append(f"Its real size: {real_size(job)}. Draw it this size.")
    return " ".join(words)


def real_size(job: Job) -> str:
    """The plan's real size, marked when it is an estimate."""
    return job.real_size + (" (an estimate from the photos)" if job.real_size_estimated else "")


def items(numbers: list[int]) -> str:
    return ("library item " if len(numbers) == 1 else "library items ") + ", ".join(
        str(number) for number in numbers
    )


def talking_facts(scene: Scene) -> list[str]:
    """A talking scene's style and product state, as scene facts, each with how it looks."""
    facts = []
    if scene.talking_style:
        looks = STYLE_LOOKS.get(scene.talking_style, "")
        facts.append(f"  talking style: {scene.talking_style}" + (f" ({looks})" if looks else ""))
    if scene.product_state:
        looks = STATE_LOOKS.get(scene.product_state, "")
        facts.append(f"  product state: {scene.product_state}" + (f" ({looks})" if looks else ""))
    if scene.packaging_photo is not None:
        facts.append(f"  packaging photo: library item {scene.packaging_photo}")
    return facts


def _scene_facts(scene: Scene, *, last: bool = False) -> str:
    """The scene as the scene agent reads it in the whole plan; `last` for the last scene of
    an ad of two or more."""
    facts = [f"Scene {scene.number}", f'  line: "{scene.line}"']
    facts.append(f"  scene kind: {scene.kind}")
    # Only the first scene is the hook and only the last the call to action: a scene added,
    # removed or moved keeps its stored part, which may no longer be where it plays.
    if scene.number == 1:
        facts.append(f"  {HOOK_PART} (the ad's first line, which makes the viewer stop and watch)")
    elif last:
        facts.append(
            f"  {LAST_PART} (the ad's last line: the after, how it feels now answering the "
            "hook, then the casual ask)"
        )
    elif scene.part and scene.part not in ("hook", "call to action"):
        facts.append(f"  part: {scene.part}")
    if scene.overlay:
        facts.append(f'  overlay: "{scene.overlay}" (code draws this on the finished ad)')
    if scene.kind_why:
        facts.append(f"  why this kind: {scene.kind_why}")
    if scene.kind == Scene.Kind.TALKING:
        facts += talking_facts(scene)
    if scene.shows:
        facts += [
            f"  shows: {scene.shows}",
            f"  kind: {scene.broll_kind}; face: {scene.person_shown}",
            *([f"  how the product is used: {scene.usage}"] if scene.usage else []),
            *([f"  ends on: {scene.result}"] if scene.result else []),
        ]
        pictures = scene_references.tier_2(scene)
        if pictures:
            facts.append(
                "  its pictures: "
                + ", ".join(f"library item {p.number} ({p.for_})" for p in pictures)
            )
        if scene.excluded:
            facts.append(
                "  never sent for it (excluded): library item "
                + ", ".join(str(number) for number in scene.excluded)
            )
    else:
        facts.append("  the presenter says it to camera")
    return "\n".join(facts)


def sent_list_words(sent: library.SentList, whose: str = "the failed clip") -> str:
    """What a clip was sent, as a list in words: each picture with its library number and
    label, each piece with its video and moment, what was left out and why, and the prompt."""
    lines = [f"What {whose} was sent, every picture in {sent.colour}:"]
    if sent.end_picture is not None:
        lines.append(f"End picture: {sent.end_picture.file}")
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
        f"Not sent: library item {left.library_number}. {left.label} (it shows a stranger's "
        "face that couldn't be changed to the presenter's, so no clip is sent it)"
        for left in sent.strangers_left_out
    ]
    lines += [
        f"Library item {each.of} couldn't be made {each.colour}, so it went as it was."
        for each in sent.recolours
        if each.outcome == "fell_back"
    ]
    lines.append(f"The prompt it was sent: {sent.prompt}")
    return "\n".join(lines)


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
    if ruling.get("detail_not_shown"):
        detail = str(ruling["detail_not_shown"]).strip().rstrip(".")
        lines.append(f"The judge left out one detail the references never show: {detail}.")
    motion = (ruling.get("answers") or {}).get("motion") or []
    if motion and isinstance(motion[0], dict):
        first = motion[0]
        look = first.get("look") or {}
        for field, label in (
            ("in_the_clip", "In the clip"),
            ("start", "At the start"),
            ("end", "At the end"),
        ):
            if look.get(field):
                lines.append(f"{label}: {look[field]}")
        if first.get("ordered_action"):
            lines.append(f"The action ordered: {first['ordered_action']}")
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


def try_parts(
    fixing: AgentFix, number: int
) -> tuple[ProducedItem | None, dict[str, Any] | None] | None:
    """Try `number` of `fixing` as its clip and the judge's ruling on it, or None when it has
    no such try. Try 0 is the clip it fixes, with the ruling handed over."""
    if number == 0:
        return fixing.first_clip, fixing.ruling
    row = fixing.tries.filter(number=number).first()
    if row is None:
        return None
    return row.clip, row.clip.ruling if row.clip is not None else None


def try_shown(label: str, clip: ProducedItem | None) -> tuple[str, list[Image]]:
    """What a look at a try shows, and what code says of what its clip was sent: when its
    clip carries a sent list (spec #210 point 21), the pictures it was sent with their labels
    and each piece's frames, then the clip's frames; else the clip's frames, with nothing
    said."""
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
    return "", clip_frames


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


def _library_room() -> int:
    """How many of a turn's pictures may be library pictures the agent opened."""
    return int(MOST_PICTURES * LIBRARY_SHARE)


HOW_TO_CALL = "how-to-photos"
HOW_TO_RULE = (
    "From the app: the page's how-to photos, shown because this scene uses the product. "
    + HOW_TO_METHOD_ONLY
)
HOW_TO_IN_VIEW = "(This how-to photo is already in view: the app showed it at the start.)"


def uses_product(scene: Scene) -> bool:
    """Whether `scene`'s action uses the product: a B-roll that does a job or has a usage,
    or a talking scene with the product in use."""
    if scene.kind == Scene.Kind.BROLL:
        return scene.broll_kind == Scene.BrollKind.DOES_A_JOB or bool(scene.usage.strip())
    return scene.product_state == "in use"


def how_to_items(scene: Scene) -> list[library.LibraryEntry]:
    """The library's how-to photos when `scene` uses the product; else none."""
    if not uses_product(scene):
        return []
    return library_sheets.how_to_photos(library.library_of(scene.job))


def how_to_shown(scene: Scene) -> ToolUse | None:
    """The how-to photos put in front of the agent at the start of the scene, or None. More
    than the library's share of the turn's pictures leaves the agent no room to open others,
    which is posted for the developer."""
    items = how_to_items(scene)
    if not items:
        return None
    told = (
        f"Scene {scene.number}'s {len(items)} how-to photos take all {_library_room()} library "
        "pictures a turn may show: its agent can't open any other library picture."
    )
    if len(items) >= _library_room() and told not in (w["text"] for w in scene.job.warnings):
        notices.post_notice(scene.job, notices.HOW_TO_FILL_THE_ROOM, told, Message.Level.INFO)
    return ToolUse(
        call_id=HOW_TO_CALL,
        tool="how_to_photos",
        arguments={},
        result=HOW_TO_RULE
        + "\n"
        + "\n".join(f"Library item {library_items.library_line(item)}" for item in items),
        pictures=[
            Shown(label=f"Library item {item.number}: {item.label}", key=item.file)
            for item in items
        ],
    )
