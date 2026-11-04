"""The owner's optional check of one clip, kept apart from the model's verdict."""

from typing import Any

from .models import ProducedItem, Scene


def choice(clip: ProducedItem) -> str | None:
    """The owner's choice, or None while this clip has not been checked."""
    chosen = (clip.ruling or {}).get("owner_check", {}).get("choice")
    return chosen if isinstance(chosen, str) else None


def reason(clip: ProducedItem) -> str:
    """What the judge could not see, including refused frames and unavailable words."""
    ruling = clip.ruling or {}
    reasons = [ruling.get("cannot_tell"), ruling.get("detail_not_shown")]
    reasons += [
        answer.get("references_never_show")
        for answer in ruling.get("answers", {}).get("product", [])
        if answer and answer.get("verdict") == "unsure"
    ]
    if ruling.get("refused_frames"):
        reasons.append("The safety filter blocked some of the clip's frames.")
    if ruling.get("word_check", {}).get("verdict") == "unchecked":
        reasons.append("The spoken words could not be checked.")
    return (
        "; ".join(dict.fromkeys(str(one).strip() for one in reasons if one))
        or "The clip could not be checked."
    )


def note(clip: ProducedItem, words: str) -> str:
    """A normal scene note carrying the owner's choice, judge's uncertainty and own words."""
    assert clip.scene is not None
    text = (
        f"Scene {clip.scene.number}: This clip looks wrong. "
        f"The judge could not check: {reason(clip)}"
    )
    return text + (f"\nOwner note: {words}" if words else "")


def context_for(scene: Scene, quotes: list[dict[str, Any]]) -> str:
    """Keep all three facts with a run even when its producer quotes only part of the note."""
    message_ids = [quote["message_id"] for quote in quotes if "message_id" in quote]
    if not message_ids:
        return ""
    clips = scene.produced.filter(ruling__owner_check__message__in=message_ids)
    return "\n".join(
        note(clip, (clip.ruling or {})["owner_check"]["note"])
        for clip in clips
        if choice(clip) == "wrong"
    )
