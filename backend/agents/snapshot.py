"""The ad as it stands (spec #247 point 15): a compact snapshot of the ad, given to the producer
last on every turn, so it knows the ad's state however much of its conversation is folded:
the plan, and per scene its versions, what each clip was sent and what it cost. The producer
reads any part in full with its read_ad tool (`scene_in_full`).

Built from the rows of finished work only, so the same rows give the same snapshot, and a
turn paid for before a worker stopped is handed the same words when it is taken again."""

from decimal import Decimal

from django.db.models import Sum

from chat.models import Session
from gateway.models import ModelCall
from jobs import work
from jobs.library import PictureRef, SentList, sent_list_of
from jobs.models import Job, Scene, SceneStep

from .money import dollars

# How the snapshot begins, so the producer reads it as the app's and knows what it is.
AD_AS_IT_STANDS = (
    "From the app, not the shop owner: the ad as it stands now, given last on every turn. Your "
    "oldest tool calls may be folded to one line each; read_ad gives any part of the ad in "
    "full, and read_earlier_call the whole answer of a folded call."
)


def ad_as_it_stands(session: Session) -> str:
    """The snapshot of the session's ad; blank while it has none planned."""
    job = session.jobs.first()
    if job is None or not job.scenes.exists():
        return ""
    scenes = list(job.scenes.order_by("number"))
    lines = [AD_AS_IT_STANDS, f"The plan: {len(scenes)} scenes."]
    lines += [_planned(scene) for scene in scenes]
    for scene in scenes:
        lines.append(f"Scene {scene.number}: {_versions(scene)} It cost {dollars(_cost(scene))}.")
    lines.append(f"Spent on the ad so far: {dollars(_spent(session, scenes))}.")
    return "\n".join(lines)


def scene_in_full(scene: Scene) -> str:
    """Everything kept of a scene: its plan, each version, and each clip's whole sent list."""
    lines = [f"Scene {scene.number} ({scene.status}). {_planned(scene)}"]
    found = work.versions(scene)
    if not found:
        lines.append("No version made yet.")
    for version in found:
        lines.append(f"{version.label}: {_made(version)}.")
        sent = sent_list_of(version.clip) if version.clip is not None else None
        if sent is not None:
            lines += _sent_in_full(sent)
    running = scene.steps.filter(status=SceneStep.Status.RUNNING).values_list("kind", flat=True)
    if running:
        lines.append(f"Being made now: {', '.join(running)}.")
    lines.append(f"Its finished work cost {dollars(_cost(scene))}.")
    return "\n".join(lines)


def _planned(scene: Scene) -> str:
    return (
        f"{scene.number}. {scene.line}"
        + (f" (Shows, while the voice says it: {scene.shows})" if scene.shows else "")
        + (f' Overlay: "{scene.overlay}".' if scene.overlay else "")
    )


def _versions(scene: Scene) -> str:
    found = work.versions(scene)
    if not found:
        return "no version yet."
    return (
        "; ".join(f"{version.label} ({_made(version)}{_sent(version)})" for version in found) + "."
    )


def _made(version: work.Version) -> str:
    made = [name for name, item in (("picture", version.picture), ("clip", version.clip)) if item]
    return " and ".join(made) if made else "nothing shown yet"


def _sent(version: work.Version) -> str:
    """What the version's clip was sent, compactly: its pictures by library number, its
    pieces, and what was left out."""
    sent = sent_list_of(version.clip) if version.clip is not None else None
    if sent is None:
        return ""
    said = f"; its clip was sent library items {', '.join(_named(each) for each in sent.pictures)}"
    if sent.pieces:
        said += ", page video pieces " + ", ".join(
            f"video {piece.video} moment {piece.moment}" for piece in sent.pieces
        )
    if sent.left_out:
        said += "; left out: " + ", ".join(
            f"library item {each.library_number}" if each.library_number else _named(each)
            for each in sent.left_out
        )
    return said


def _named(picture: PictureRef) -> str:
    if picture.library_number is not None:
        return str(picture.library_number)
    return "the room alone" if picture.drawn == "room" else "the person in the room"


def _sent_in_full(sent: SentList) -> list[str]:
    lines = [f"Its clip was sent, in {sent.colour}:"]
    for each in sent.pictures:
        what = f", for {each.for_}" if each.for_ else ""
        lines.append(f"Image {each.image} (tier {each.tier}{what}): {each.label}")
    for piece in sent.pieces:
        trim = f" ({piece.trim})" if piece.trim else ""
        lines.append(
            f"Video {piece.number}: page video {piece.video}, moment {piece.moment}, "
            f"{piece.start:g}-{piece.end:g} s{trim}"
        )
    lines += [f"Left out: {each.label} (tier {each.tier})" for each in sent.left_out]
    lines += [
        f"Recolour of library item {each.of} to {each.colour}: {each.outcome}"
        for each in sent.recolours
    ]
    lines.append(f"Its prompt: {sent.prompt}")
    return lines


def _cost(scene: Scene) -> Decimal:
    """What the scene's finished steps cost, the fixing agent's work on them included."""
    finished = scene.steps.filter(finished_at__isnull=False)
    return sum((step.cost_usd() for step in finished), Decimal(0))


def _spent(session: Session, scenes: list[Scene]) -> Decimal:
    """What the ad's finished work has cost: every scene's, and every finished tool call that
    started no scene step. The agents' own turns aside."""
    calls: Decimal | None = ModelCall.objects.filter(
        tool_call__session=session,
        tool_call__finished_at__isnull=False,
        tool_call__scene_steps__isnull=True,
        tool_call__agent_fix__isnull=True,
    ).aggregate(cost=Sum("cost_usd"))["cost"]
    return (calls or Decimal(0)) + sum((_cost(scene) for scene in scenes), Decimal(0))


def ad_in_full(job: Job, plan: str) -> str:
    """The whole ad: its `plan` as the producer is told it, then every scene in full."""
    return "\n\n".join([plan, *(scene_in_full(scene) for scene in job.scenes.order_by("number"))])
