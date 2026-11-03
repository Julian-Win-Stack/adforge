"""Open a library picture or watched video moment in the scene chat."""

from typing import ClassVar

from pydantic import Field

from agents.loop import Refused, Tool, ToolResult
from agents.models import ToolCall
from gateway.types import (
    Image,
)
from jobs import (
    clip_judge,
    library,
    library_items,
    product_videos,
)
from jobs.sent_labels import PIECE_LABEL

from . import lifecycle, media

LIBRARY_FULL = (
    "the library pictures you have open fill the room kept for them, so nothing more can be "
    "opened in this scene's fix: work from what you have open, or open one of those again."
)


class LookAtLibrary(Tool):
    """Open one item of the ad's picture library by its number, to see the picture; or one
    moment of a page video, by the video's number and the moment's, to see its frames. What
    you open stays in front of you for the rest of this scene's fix."""

    name: ClassVar[str] = "look_at_library"
    item: int | None = Field(description="The library item's number; null for a moment.")
    video: int | None = Field(description="For a moment: the page video's number; else null.")
    moment: int | None = Field(description="For a moment: its number in that video; else null.")

    def run(self, call: ToolCall) -> ToolResult:
        assert call.agent_fix is not None
        job = call.agent_fix.step.scene.job
        an_item = self.item is not None and self.video is None and self.moment is None
        a_moment = self.item is None and self.video is not None and self.moment is not None
        if not (an_item or a_moment):
            raise Refused("name a library item, or a video and its moment, not both.")
        if self.item is not None:
            found = [each for each in library.library_of(job) if each.number == self.item]
            if not found:
                raise Refused(f"there is no library item {self.item}.")
            [item] = found
            return _within_the_room(
                call,
                ToolResult(
                    f"Library item {library_items.library_line(item)}",
                    [Image(label=f"Library item {item.number}: {item.label}", key=item.file)],
                ),
            )
        assert self.video is not None and self.moment is not None
        video, _, moment = media._moment(job, self.video, self.moment)
        named = f"Page video {self.video}, moment {self.moment}"
        part = product_videos.cut_part(video.file, moment.start, moment.end, sound=False)
        # Each frame at its second in the video, so it reads as the moment's seconds do.
        frames = [
            {**frame, "times": [round(moment.start + t, 3) for t in frame["times"]]}
            for frame in clip_judge.piece_frames(part)
        ]
        return _within_the_room(
            call,
            ToolResult(
                f"{named}, {moment.start:g} s to {moment.end:g} s: {moment.happens} On screen: "
                f"{moment.on_screen} Its frames, each at its second in the video, "
                f"{PIECE_LABEL}.",
                [
                    Image(label=each.label, key=each.key)
                    for each in media._four_to_a_picture(frames, named)
                ],
            ),
        )


def _within_the_room(call: ToolCall, opened: ToolResult) -> ToolResult:
    """`opened`, unless its pictures would take the library pictures the agent has open past
    LIBRARY_ROOM: then refused, in words. A look at one already open is always let through,
    as it is shown once."""
    fixing = call.agent_fix
    assert fixing is not None
    looks = {
        tuple(picture["key"] for picture in each.pictures)
        for each in lifecycle._calls(fixing).filter(
            tool=LookAtLibrary.name, finished_at__isnull=False
        )
        if each.pictures
    }
    keys = tuple(picture.key for picture in opened.pictures)
    if keys in looks:
        return opened
    if sum(len(each) for each in looks) + len(keys) > media._library_room():
        raise Refused(LIBRARY_FULL)
    return opened
