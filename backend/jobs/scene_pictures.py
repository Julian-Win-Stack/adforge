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
    drawn_from = _place_inputs(picture)
    if drawn_from:
        from .work import place_inputs_now

        if drawn_from not in place_inputs_now(scene):
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
    while True:
        record = picture.sent or {}
        if not all(
            originals.get(int(photo["library_number"])) == photo.get("original_file", photo["file"])
            for photo in record.get("photos", [])
            # A page view code added never makes a picture stale: it isn't the agent's pick
            # (agents.scene_agent.page_views), and each call adds the views afresh.
            if photo.get("library_number") and not photo.get("page_view")
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


def _place_inputs(picture: ProducedItem) -> list[str]:
    """The produced pictures (no library photo) a starting picture was drawn from, in the order
    sent. An edit sends none of its own; it keeps those of the picture it edits."""
    while True:
        record = picture.sent or {}
        files = [
            str(photo["file"])
            for photo in record.get("photos", [])
            if not photo.get("library_number")
        ]
        if files:
            return files
        previous = ProducedItem.objects.filter(pk=record.get("edited_from") or 0).first()
        if previous is None:
            return []
        picture = previous


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
