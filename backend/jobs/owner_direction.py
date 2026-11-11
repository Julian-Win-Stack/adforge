"""Owner steering keeps checked words separate from the ad's presentation defaults."""

from agents.owner_quotes import QuoteNotFound, check_session_quotes
from chat.models import Message

from . import notices
from .models import Job, Scene, SceneAgentRun
from .notices import post_notice

OWNER_WORDS_RULE = (
    "The hook, spoken-line style, closing line, scene kinds and order, middle B-roll and "
    "showing to the lens are defaults for the first ad. After the first ad, the owner's "
    "clear request wins over those defaults. Keep their checked exact words, especially "
    "a line or script they gave verbatim; never rewrite it just to fit a default. Only a "
    "real fact problem or video-model limit may require changing it; explain which line "
    "changed and why. Facts must still match the page or the owner's words; never invent "
    "facts, change the product or bypass the video model's limits or picture evidence. "
    "When a note does not say what to change, ask one short question with 2 or 3 concrete "
    "options and spend nothing until the owner answers."
)


def owner_steering(job: Job, owner_asked: bool) -> bool:
    """Default presentation rules yield only to owner changes after the first ad."""
    return owner_asked and job.produced.filter(kind="finished_ad").exists()


def words_to_keep(scene: Scene) -> list[str]:
    """Checked scene notes and any exact line the owner supplied, each kept verbatim."""
    if not scene.job.produced.filter(kind="finished_ad").exists():
        return []
    words: list[str] = list(scene.owner_words)
    run = (
        SceneAgentRun.objects.filter(agent_fix__step__scene=scene)
        .order_by("-created_at", "-pk")
        .first()
    )
    if run is not None:
        words.extend(quote["text"] for quote in run.quotes)
    if is_owner_line(scene, scene.line):
        words.append(scene.line)
    return list(dict.fromkeys(words))


def remember_line(scene: Scene, *, replace: bool = False) -> None:
    """Keep a checked exact line across rewrites; a new owner script replaces the old one."""
    if (scene.owner_words and not replace) or not owner_steering(scene.job, True):
        return
    scene.owner_words = [scene.line] if is_owner_line(scene, scene.line) else []


def tell_line_change(
    scene: Scene, before: str, after: str | None, reason: str, words: list[str]
) -> None:
    """Tell the owner when a real limit required changing their checked words."""
    if not words or (after is not None and " ".join(before.split()) == " ".join(after.split())):
        return
    if not scene.owner_words and not any(
        text.casefold() in before.casefold() or before.casefold() in text.casefold()
        for text in words
        if text.strip()
    ):
        return
    change = f'I left out "{before}"' if after is None else f'I changed "{before}" to "{after}"'
    post_notice(
        scene.job,
        notices.OWNER_LINE_CHANGED,
        f"Scene {scene.number}: {change} because {reason}",
        Message.Level.INFO,
        scene=scene.number,
    )


def owner_changed_scene(scene: Scene) -> bool:
    """Whether the scene’s checked line or latest run is an owner change."""
    if scene.owner_words:
        return True
    run = (
        SceneAgentRun.objects.filter(agent_fix__step__scene=scene)
        .order_by("-created_at", "-pk")
        .first()
    )
    return run is not None and any(quote.get("owner_asked") for quote in run.quotes)


def is_owner_line(scene: Scene, line: str) -> bool:
    """Whether this exact line can be checked against an owner message."""
    session = scene.job.session
    if session is None:
        return False
    try:
        check_session_quotes(session, [line])
    except QuoteNotFound:
        return False
    return True
