"""Current inputs and owner visibility of scene-agent starting pictures."""

from . import library_items
from .library import PagePhoto
from .models import ProducedItem, Scene, SceneStep


def from_agent(picture: ProducedItem) -> bool:
    return picture.step is not None and picture.step.kind == SceneStep.Kind.SCENE_AGENT


def current(scene: Scene) -> ProducedItem | None:
    picture = (
        scene.produced.filter(
            kind=ProducedItem.Kind.STARTING_PICTURE, step__kind=SceneStep.Kind.SCENE_AGENT
        )
        .order_by("-created_at", "-pk")
        .first()
    )
    clip = (
        scene.produced.filter(
            kind=ProducedItem.Kind.CLIP,
            picture__step__kind=SceneStep.Kind.SCENE_AGENT,
            step__status=SceneStep.Status.FINISHED,
        )
        .select_related("picture")
        .order_by("-created_at", "-pk")
        .first()
    )
    if (
        clip is not None
        and clip.picture is not None
        and (picture is None or clip.created_at > picture.created_at)
    ):
        picture = clip.picture
    if picture is None or not matches(picture, scene):
        return None
    return picture


def matches(picture: ProducedItem, scene: Scene) -> bool:
    record = picture.sent or {}
    if any(getattr(scene, field) != value for field, value in record.get("scene", {}).items()):
        return False
    entries = library_items.originals(scene.job)
    originals = {item.number: item.file for item in entries}
    page_photos = dict(scene.job.photos.values_list("position", "file"))
    for item in entries:
        if isinstance(item.source, PagePhoto) and page_photos.get(item.source.photo) != item.file:
            originals.pop(item.number, None)
    return all(
        originals.get(int(photo["library_number"])) == photo.get("original_file", photo["file"])
        for photo in record.get("photos", [])
        if photo.get("library_number")
    )


def visible(picture: ProducedItem) -> bool:
    scene = picture.scene
    assert scene is not None
    clips = scene.produced.filter(
        kind=ProducedItem.Kind.CLIP, step__status=SceneStep.Status.FINISHED
    )
    if clips.filter(picture=picture).exists():
        return True
    if clips.exists():
        return False
    newest = (
        scene.produced.filter(
            kind=ProducedItem.Kind.STARTING_PICTURE, step__kind=SceneStep.Kind.SCENE_AGENT
        )
        .order_by("-created_at", "-pk")
        .first()
    )
    return newest == picture
