"""The scene agent's tools and instructions for a silent scene: a product-shots ad's scene,
with no presenter and no spoken line (#770). Its starting picture is drawn from the product
photos by the B-roll path with no shared place (draw_starting_picture.make_picture), and its
clip is a slow camera move between that picture and a crop of it, made, judged and settled as
any try is (film.py)."""

import math
from typing import ClassVar

from pydantic import Field

from agents.loop import Refused, Tool, ToolResult
from agents.models import ToolCall
from agents.scene_forms import (
    PULL_BACK,
    PUSH_IN,
    CameraMove,
    SilentPictureForm,
    SilentVideoForm,
    has_quoted_text,
    silent_video_prompt,
)
from jobs import cut_and_send, library, scene_pictures
from jobs.models import ProducedItem, Scene
from jobs.pace import LONGEST_CLIP_SECONDS, SHORTEST_CLIP_SECONDS

from . import lifecycle, page_views
from . import still_shot as stills
from .draw_starting_picture import Pick, current_picture, make_picture
from .film import close_try, render_try, submit_try
from .photos import evidence_photos

SILENT_INSTRUCTIONS = """\
You own one silent scene of a product-shots ad: tall, no presenter, no spoken line, no voice. \
The ad is beautiful product shots, overlays drawn by code and music. Your scene's plan says \
what it shows, the library photo it starts from, the camera move and its length.

First call draw_silent_picture with picks of the product photos (at least the planned one) \
and the picture form. Draw the product exactly as the photos show it, large in the frame, \
whole, in a setting that matches the product's style and the plan. A part no photo shows is \
drawn in its most ordinary look. No presenter; a hand or a worn product on a body is fine \
when the plan says so, with no face unless the photo it starts from shows one.

Then film_silent: the clip is a slow camera move between the starting picture and a crop of \
it around crop_x, crop_y, so the product can't drift: "push in" ends close on the detail, \
"pull back" starts close on it. Light may move over the product (a slow sweep, a glint). \
Nothing else moves: never ask the product to turn, swing, open or lift. Write no spoken \
words and no quoted text in any box. Read the clip judge's answer and the frames: when it \
fails, fix the cause (a new picture, a different crop centre or move, a shorter light \
description) and film again. Every picture and clip result says how many tries are left. \
Call give_up with a plain reason, for the producer only, once nothing more can be tried. \
Never ask the owner for anything. A turn must call a tool."""


def scene_facts(scene: Scene) -> str:
    """A silent scene as the scene agent reads it in the whole plan."""
    plan = scene.silent or {}
    facts = [
        f"Scene {scene.number} (silent)",
        f"  shows: {scene.shows}",
        f"  starts from: library item {plan['photo']}",
        f"  camera move: {plan['move']}",
        f"  length: {plan['seconds']} s",
    ]
    if scene.overlay:
        facts.append(f'  overlay: "{scene.overlay}" (code draws this on the finished ad)')
    return "\n".join(facts)


def clip_seconds(scene: Scene) -> int:
    """The whole seconds the video model films for the scene: its planned length, rounded
    up, within what the model films. Assembly cuts it to the planned length."""
    seconds = float((scene.silent or {})["seconds"])
    return min(LONGEST_CLIP_SECONDS, max(SHORTEST_CLIP_SECONDS, math.ceil(seconds)))


class DrawSilentPicture(Tool):
    """Draw this silent scene's starting picture from the product photos you pick, filling
    the picture form. The picture check answers; you see the picture."""

    name: ClassVar[str] = "draw_silent_picture"
    picks: list[Pick] = Field(min_length=1)
    form: SilentPictureForm

    def run(self, call: ToolCall) -> ToolResult:
        return make_picture(call, self.form, self.picks)


class FilmSilent(Tool):
    """Film a passed or unchecked starting picture of this silent scene as a slow camera move
    between the picture and a crop of it around crop_x, crop_y. The clip judge answers; you
    see its frames."""

    name: ClassVar[str] = "film_silent"
    picture: int | None = Field(
        default=None, description="Starting picture ID; omit for the current working picture."
    )
    move: CameraMove = Field(description='"push in" or "pull back", as the plan says.')
    crop_x: int = Field(
        default=50, ge=0, le=100, description="The crop's centre: percent of width from left."
    )
    crop_y: int = Field(
        default=50, ge=0, le=100, description="The crop's centre: percent of height from top."
    )
    form: SilentVideoForm

    def run(self, call: ToolCall) -> ToolResult:
        from . import current_run

        assert call.agent_fix is not None
        fixing = call.agent_fix
        scene = fixing.step.scene
        row = fixing.tries.filter(tool_call=call).first()
        if row is None:
            window = current_run(fixing)
            picture = current_picture(call)
            if self.picture is not None:
                picture = scene.produced.filter(
                    kind=ProducedItem.Kind.STARTING_PICTURE, pk=self.picture
                ).first()
                if picture is None:
                    raise Refused("Name a starting picture from this scene.")
            if picture is None or (picture.ruling or {}).get("status") not in {
                "passed",
                "unchecked",
            }:
                raise Refused(
                    "Film only after the selected starting picture has passed its check "
                    "or gone unchecked."
                )
            if not scene_pictures.matches(picture, scene):
                raise Refused("The starting picture is out of date; draw and check a new picture.")
            if window.clips >= lifecycle.clip_limit(window):
                raise Refused("This run has finished filming its clips.")
            if any(has_quoted_text(box) for box in self.form.model_dump().values()):
                raise Refused("The video form has quoted text; a silent clip has no words.")
            first, last = stills.frames(
                picture.file,
                PUSH_IN if self.move == "push in" else PULL_BACK,
                self.crop_x,
                self.crop_y,
            )
            prompt = silent_video_prompt(self.form, self.move)
            # The clip judge gets the photos and page views the picture was drawn with.
            photos = [
                *(photo for photo in evidence_photos(picture) if photo.library_number is not None),
                *page_views.drawn_with(picture),
            ]
            colour = cut_and_send.planned_colour(scene)
            labels = page_views.judge_labels(scene.job, photos, colour, scene=scene.number)
            sent = library.SentList(
                colour=colour,
                pictures=[],
                pieces=[],
                left_out=[],
                recolours=[],
                prompt=prompt,
                mode="start_frame",
                starting_picture=library.StartingPicture(file=first),
                end_picture=library.StartingPicture(file=last),
                scene_run=window.pk,
                picture_photos=[
                    library.SentPicture(
                        image=number,
                        tier=library.PAGE_VIEW_TIER if photo.page_view else 2,
                        library_number=photo.library_number,
                        drawn=None,
                        for_=None,
                        label=label,
                        file=photo.file,
                    )
                    for number, (photo, label) in enumerate(zip(photos, labels, strict=True), 1)
                ],
                seconds=clip_seconds(scene),
            )
            row = submit_try(fixing, call, window, picture, prompt, sent)
        # A silent clip has no spoken words to read.
        window = render_try(fixing, row, transcribe=False)
        return close_try(fixing, row, window)
