"""Selected drawing inputs and the evidence inherited by an edited picture."""

import re

from pydantic import BaseModel, field_validator

from chat.models import Message
from jobs import notices
from jobs.models import ProducedItem


class SelectedPhoto(BaseModel):
    file: str
    line: str
    # The shared person/room picture is a produced picture, not a library item.
    library_number: int | None = None
    original_file: str | None = None
    # TRY: a cropped close-up of the photo just before it.
    close_up: bool | None = None

    @field_validator("library_number", mode="before")
    @classmethod
    def _old_shared_number(cls, value: object) -> object:
        return None if value == "" else value


def evidence_photos(
    picture: ProducedItem, *, adding: list[SelectedPhoto] | None = None
) -> list[SelectedPhoto]:
    """Oldest first, with each library item's newest file and line in its first position.

    `adding` is the current edit's inputs, before that picture has been saved. Stored
    `photos` always remain the inputs sent to the picture model for that call.
    """
    assert picture.scene_id is not None
    chain: list[list[SelectedPhoto]] = []
    seen: set[int] = set()
    current = picture
    while True:
        seen.add(current.pk)
        record = current.sent or {}
        chain.append([SelectedPhoto.model_validate(photo) for photo in record.get("photos", [])])
        source = record.get("edited_from")
        if not source:
            break
        previous = ProducedItem.objects.filter(
            pk=source, scene_id=picture.scene_id, kind=ProducedItem.Kind.STARTING_PICTURE
        ).first()
        problem = (
            f"source picture {source} could not be found in this scene"
            if previous is None
            else f"the edit chain repeats picture {source}"
            if previous.pk in seen
            else ""
        )
        if problem:
            notices.post_notice(
                picture.job,
                notices.PICTURE_EVIDENCE_INCOMPLETE,
                f"Starting picture {picture.pk}: {problem}; only the evidence photos "
                "found in the edit chain were kept.",
                Message.Level.PROBLEM,
            )
            break
        assert previous is not None
        current = previous
    if len(chain) == 1 and adding is None and not (picture.sent or {}).get("edited_from"):
        return chain[0]
    # Dict replacement keeps the first position while taking the newest photo. Shared
    # person/room pictures have no library number and are identified by their file.
    kept: dict[int | str, SelectedPhoto] = {}
    for photos in [*reversed(chain), adding or []]:
        for photo in photos:
            key = photo.library_number if photo.library_number is not None else photo.file
            kept[key] = photo
    return [
        photo.model_copy(update={"line": re.sub(r"^Image \d+\b", f"Image {number}", photo.line)})
        for number, photo in enumerate(kept.values(), 1)
    ]
