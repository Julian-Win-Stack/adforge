"""Stale steps (spec #247 point 9): a scene step whose inputs changed since it ran. Nothing is
flagged: a step's snapshot of its scene (SceneStep.snapshot) no longer matching the scene as
it stands is what stale means. A new line makes the audio, its transcript and the clip stale;
what the scene shows, or its B-roll labels, the starting picture and the clip; a new
reference list the clip. Code only says so: the producer makes again what it decides to,
with the scene tools, and nothing is made again on its own."""

from .models import ProducedItem, Scene, SceneStep

# The order the producer is told them in, which is the order they are made in.
STEP_NAMES = {
    SceneStep.Kind.STARTING_PICTURE: "starting picture",
    SceneStep.Kind.LINE_AUDIO: "audio",
    SceneStep.Kind.TRANSCRIPT: "transcript",
    SceneStep.Kind.CLIP: "clip",
}


def _finished(scene: Scene, kind: SceneStep.Kind) -> bool:
    """Whether the scene has anything of `kind` a step finished making."""
    return scene.produced.filter(
        kind=kind, step__kind=kind, step__status=SceneStep.Status.FINISHED
    ).exists()


def current_audio(scene: Scene) -> ProducedItem | None:
    """The scene's audio of its line as it stands, in the person's voice as it stands."""
    voice = scene.job.produced.filter(kind=ProducedItem.Kind.VOICE).order_by("version").last()
    return (
        scene.produced.filter(
            kind=ProducedItem.Kind.LINE_AUDIO,
            step__status=SceneStep.Status.FINISHED,
            step__line=scene.line,
            made_from=voice,
        )
        .order_by("version")
        .last()
    )


def current_clip(scene: Scene) -> ProducedItem | None:
    """The scene's newest clip made for the scene as it stands: its line, what it shows, its
    B-roll labels and its reference list, from its audio as it stands."""
    audio = current_audio(scene)
    if audio is None:
        return None
    clips = scene.produced.filter(
        kind=ProducedItem.Kind.CLIP,
        step__status=SceneStep.Status.FINISHED,
        made_from=audio,
        **SceneStep.made_for(scene, through="step__"),
    )
    picture = scene.current_picture()
    if picture is not None:
        # Of the starting picture as it stands: any version of it, one brought back being
        # the same picture (#143).
        clips = clips.filter(picture__step=picture.step)
    return clips.order_by("version").last()


def stale_steps(scene: Scene) -> list[SceneStep.Kind]:
    """The kinds of step the scene has made something of whose inputs have changed since, so
    that nothing it has of that kind is made for it as it stands, in the order they are
    made."""
    audio = current_audio(scene)
    current = {
        # A scene showing the product has no starting picture (spec #210 point 19): one made
        # while it was said to camera is never made again.
        SceneStep.Kind.STARTING_PICTURE: bool(scene.shows) or scene.current_picture() is not None,
        SceneStep.Kind.LINE_AUDIO: audio is not None,
        SceneStep.Kind.TRANSCRIPT: audio is not None
        and scene.produced.filter(
            kind=ProducedItem.Kind.TRANSCRIPT,
            step__status=SceneStep.Status.FINISHED,
            made_from=audio,
        ).exists(),
        SceneStep.Kind.CLIP: current_clip(scene) is not None,
    }
    return [kind for kind in STEP_NAMES if not current[kind] and _finished(scene, kind)]


def is_stale(step: SceneStep) -> bool:
    """Whether a step was made for its scene as it was before an edit: its snapshot no longer
    matches the scene's inputs for its kind."""
    scene = Scene.with_removed.get(pk=step.scene_id)
    made_for = (
        SceneStep.picture_made_for(scene)
        if step.kind == SceneStep.Kind.STARTING_PICTURE
        else {"line": scene.line}
        if step.kind in (SceneStep.Kind.LINE_AUDIO, SceneStep.Kind.TRANSCRIPT)
        else SceneStep.made_for(scene)
    )
    return not SceneStep.objects.filter(pk=step.pk, **made_for).exists()


def after_edit(scene: Scene) -> str:
    """Mark what an edit of `scene` made stale, and say so, for the tool's answer: a finished
    scene whose clip is stale is planned again, so the ad isn't assembled from that clip, and
    one with a clip made for it as it now stands is finished. Blank when nothing is stale."""
    stale = stale_steps(scene)
    if SceneStep.Kind.CLIP in stale:
        scene.plan_again()
    elif current_clip(scene) is not None:
        # An edit taken back: the clip made for the scene as it stands finishes it again.
        Scene.objects.filter(pk=scene.pk).update(status=Scene.Status.FINISHED)
    if not stale:
        return ""
    names = [STEP_NAMES[kind] for kind in stale]
    listed = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
    its = "is" if len(names) == 1 else "are"
    return (
        f"Scene {scene.number}'s {listed} {its} stale now: made for the scene as it was. "
        "Nothing is made again by itself."
    )
