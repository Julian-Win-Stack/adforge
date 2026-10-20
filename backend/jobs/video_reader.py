"""The video reader: each product video with a file is read once, as its page is read and
before the ad is planned. The planner's own model is given the planner's own instructions,
with one job added, and what the planner gets (the page, the product photos, the
conversation), plus the video's transcript and its picked frames in labelled grids. It names
the video moments it would need as pictures, and why, and those it wouldn't; it never plans
and never asks. Its answer is kept on the video."""

import functools
import hashlib
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from adforge import file_store
from chat.models import Message
from gateway.gateway import call_model
from gateway.types import (
    Image,
)
from jobs import picker_grids
from jobs.models import Job, ProductVideo
from jobs.notices import post_notice
from jobs.planning import PLAN_INSTRUCTIONS, PlanHandoff
from jobs.work import plan_handoff

logger = logging.getLogger(__name__)

# Julian, 2026-10-20 02:23 ("Do these things"), after the Rare Beauty test kept nothing.
ADDED_JOB = (
    "You ask the shop owner nothing. Before you plan, name the video moments you would need "
    "as pictures, and why."
)
INSTRUCTIONS = f"{PLAN_INSTRUCTIONS}\n\n{ADDED_JOB}"
# The most pictures the model is shown in one call, and the most grids of the video's frames
# among them: the page's first photos fill the rest.
MOST_PICTURES = 50
MOST_GRIDS = 5
MOST_PHOTOS = MOST_PICTURES - MOST_GRIDS
# Each grid is 4 by 4 frames, each 400 px a side under a black bar with its second.
GRID_ACROSS = 4
TILE_SIDE = 400
GRID_KIND = "The product's video"
MOST_FRAMES = MOST_GRIDS * GRID_ACROSS * GRID_ACROSS


class VideoReadHandoff(PlanHandoff):
    """What the planner is handed, and the words spoken in the video."""

    transcript: str


class KeptMoment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    second: float = Field(description="The second its picture's black bar gives.")
    shows: str = Field(description="What this shows that no photo shows, in one line.")
    why: str = Field(description="Why you would need it as a picture.")


class RejectedMoment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    second: float = Field(description="The second its picture's black bar gives.")
    why: str = Field(description="Why you would not need it, in a few words.")


class VideoRead(BaseModel):
    """The video moments the reader would need as pictures, and those it wouldn't."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kept: list[KeptMoment]
    rejected: list[RejectedMoment]


class NoShownMoment(Exception):
    """The reader named moments, but none at a second any shown frame carries."""


def on_shown_frames(answer: VideoRead, labels: set[str]) -> dict[str, Any]:
    """The answer as kept on the video: its moments at a second a shown frame is labelled
    with, and the others dropped, by their seconds. An answer that named moments but none
    a frame shows can't be used."""
    kept = [moment for moment in answer.kept if second_label(moment.second) in labels]
    rejected = [moment for moment in answer.rejected if second_label(moment.second) in labels]
    named: list[KeptMoment | RejectedMoment] = [*answer.kept, *answer.rejected]
    dropped = [
        {"second": moment.second} for moment in named if second_label(moment.second) not in labels
    ]
    if dropped and not kept and not rejected:
        raise NoShownMoment(
            "no moment it named is at a second a shown picture is labelled with: "
            + ", ".join(second_label(each["second"]) for each in dropped)
        )
    return {
        "kept": [moment.model_dump(mode="json") for moment in kept],
        "rejected": [moment.model_dump(mode="json") for moment in rejected],
        "dropped": dropped,
    }


def second_label(second: float) -> str:
    return f"{second:g} s"


def read_videos(job: Job, page_text: str) -> list[str]:
    """Read each of the job's product videos that has picked frames, once: a video already
    read is not read again, and its call is paid for once. Gives back a line for the
    producer for each video that couldn't be read; the ad is planned without it."""
    problems: list[str] = []
    for video in job.videos.exclude(file="").order_by("position"):
        if not video.frames_picked or video.read is not None:
            continue
        try:
            video.read = _read(job, video, page_text)
        except Exception as error:
            # Whatever stops one video's read, the page is read and the ad planned without it.
            logger.exception("Product video %s couldn't be read", video.pk)
            video.read = {"error": str(error)}
            problems.append(
                f"The product's video {video.position} couldn't be read ({error}), so the ad "
                "is planned without it."
            )
            post_notice(
                job,
                f"Your product's video {video.position} couldn't be read, so the ad is "
                "planned without its pictures.",
                Message.Level.PROBLEM,
            )
        video.save(update_fields=["read"])
    return problems


def _read(job: Job, video: ProductVideo, page_text: str) -> dict[str, Any]:
    photos = sorted(job.photos.all(), key=lambda photo: (not photo.source_url, photo.position))
    shown = photos[:MOST_PHOTOS]
    frames = video.frames_picked[:MOST_FRAMES]
    grids = _grids(job, video, frames)
    images = [Image(label=f"Photo {photo.position}", key=photo.file) for photo in shown] + grids
    if len(images) > MOST_PICTURES:
        raise ValueError(f"the video reader would be shown {len(images)} pictures")
    handoff = plan_handoff(job, page_text, len(photos))
    answer = call_model(
        job=job,
        purpose="read_video",
        instructions=INSTRUCTIONS,
        handoff=VideoReadHandoff(**dict(handoff), transcript=video.transcript),
        output=VideoRead,
        images=images,
        pay_once=True,
    )
    return on_shown_frames(answer, {second_label(frame["second"]) for frame in frames})


def _grids(job: Job, video: ProductVideo, frames: list[dict[str, Any]]) -> list[Image]:
    """The video's picked frames in grids, each frame labelled with its second, each grid
    kept under a name its content gives: the same frames give the same grid, so a call
    asked again matches the one paid for."""
    per_grid = GRID_ACROSS * GRID_ACROSS
    tiles = [(second_label(frame["second"]), file_store.read(frame["key"])) for frame in frames]
    count = (len(tiles) + per_grid - 1) // per_grid
    grids: list[Image] = []
    for number, start in enumerate(range(0, len(tiles), per_grid), 1):
        part = tiles[start : start + per_grid]
        content = picker_grids.grid(part, GRID_ACROSS, TILE_SIDE)
        named_by = hashlib.sha256(content).hexdigest()[:16]
        name = f"jobs/{job.pk}/videos/{video.position}-grid-{named_by}.jpg"
        key = file_store.save_once(name, functools.partial(bytes, content), whole=content.__eq__)
        labels = ", ".join(label for label, _ in part)
        grids.append(
            Image(
                label=f"{GRID_KIND}, grid {number} of {count}: {labels} "
                "(each labelled in its black bar)",
                key=key,
            )
        )
    return grids
