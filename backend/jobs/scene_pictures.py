"""Current inputs and owner visibility of scene-agent starting pictures."""

from . import cut_and_send, library_items
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
    # An unfilmed redraw does not replace the picture of the clip kept in the ad.
    # The agent's film tool independently chooses its newest draw.
    if clip is not None and clip.picture is not None and matches(clip.picture, scene):
        return clip.picture
    if picture is None or not matches(picture, scene):
        return None
    return picture


def matches(picture: ProducedItem, scene: Scene) -> bool:
    record = picture.sent or {}
    if any(getattr(scene, field) != value for field, value in record.get("scene", {}).items()):
        return False
    if "planned_colour" in record and record["planned_colour"] != cut_and_send.planned_colour(
        scene
    ):
        return False
    shared = _shared_input(picture)
    if shared is not None:
        from .work import scene_place

        _, current_shared = scene_place(scene, scene.person_shown)
        if current_shared is None or current_shared.file != shared:
            return False
    entries = library_items.originals(scene.job)
    originals = {item.number: item.file for item in entries}
    page_photos = dict(scene.job.photos.values_list("position", "file"))
    for item in entries:
        if isinstance(item.source, PagePhoto) and page_photos.get(item.source.photo) != item.file:
            originals.pop(item.number, None)
    return _photos_match(picture, originals)


def _photos_match(picture: ProducedItem, originals: dict[int, str]) -> bool:
    """An edit inherits source photos, but its own row and colour replace the old snapshot."""
    assert picture.scene_id is not None
    seen: set[int] = set()
    while picture.pk not in seen:
        seen.add(picture.pk)
        record = picture.sent or {}
        if not all(
            originals.get(int(photo["library_number"])) == photo.get("original_file", photo["file"])
            for photo in record.get("photos", [])
            if photo.get("library_number")
        ):
            return False
        edited_from = record.get("edited_from")
        if not edited_from:
            return True
        previous = ProducedItem.objects.filter(pk=edited_from, scene_id=picture.scene_id).first()
        if previous is None:
            return False
        picture = previous
    return False


def _shared_input(picture: ProducedItem) -> str | None:
    """An edit keeps the shared look carried by its image 1, rather than a new room input."""
    record = picture.sent or {}
    shared = next(
        (photo["file"] for photo in record.get("photos", []) if not photo.get("library_number")),
        None,
    )
    if shared is not None:
        return str(shared)
    edited_from = record.get("edited_from")
    previous = ProducedItem.objects.filter(pk=edited_from).first() if edited_from else None
    return _shared_input(previous) if previous is not None else None


def visible(picture: ProducedItem) -> bool:
    # An owner-requested restore is shown even when its replacement clip costs money.
    if picture.back_to is not None:
        return True
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
