"""The scene agent's tools and instructions for a silent scene: a product-shots ad's scene,
with no presenter and no spoken line (#770). Its starting picture is the shop's own product
photo fitted to the tall frame by code, its pixels kept (#772, jobs.real_photo), or one drawn
from the product photos by the B-roll path with no shared place
(draw_starting_picture.make_picture), as the plan says; its clip is a slow camera move
between that picture and a crop of it, made, judged and settled as any try is (film.py)."""

import math
from typing import ClassVar, Literal

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
from chat.models import Message
from gateway.models import ModelCall
from gateway.types import BlockedBySafetyFilter
from jobs import cut_and_send, library, library_items, notices, real_photo, scene_pictures
from jobs.models import ProducedItem, Scene
from jobs.pace import LONGEST_CLIP_SECONDS, SHORTEST_CLIP_SECONDS

from . import lifecycle, page_views
from . import still_shot as stills
from .draw_starting_picture import Pick, current_picture, keep_picture, make_picture, result
from .film import close_try, render_try, submit_try
from .photos import SelectedPhoto, evidence_photos
from .picture_check import check

# What a silent scene starts on, as its plan says.
Start = Literal["real photo", "ai scene"]
REAL_PHOTO: Start = "real photo"
AI_SCENE: Start = "ai scene"

SILENT_INSTRUCTIONS = """\
You own one silent scene of a product-shots ad: tall, no presenter, no spoken line, no voice. \
The ad is beautiful product shots, overlays drawn by code and music. Your scene's plan says \
what it shows, the library photo it starts from, whether it starts on the real photo or an \
AI scene, the camera move and its length.

A real-photo scene: call use_real_photo with focus_x and focus_y on the product (percent \
of the photo's width and height), so a crop keeps it whole. Code fits the shop's own photo \
to the tall frame and keeps its pixels, so the buyer sees the exact product. If code can't \
use the photo, it says so, the scene becomes an AI scene and you draw it instead.
An AI scene: call draw_silent_picture with picks of the product photos (at least the \
planned one) and the picture form. Draw the product exactly as the photos show it, large \
in the frame, whole, in a setting that matches the product's style and the plan. A part no \
photo shows is drawn in its most ordinary look. No presenter; a hand or a worn product on a \
body is fine when the plan says so, with no face unless the photo it starts from shows one.

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
        f"  starts from: library item {plan['photo']}, {starts_on(scene)}",
        f"  camera move: {plan['move']}",
        f"  length: {plan['seconds']} s",
    ]
    if scene.overlay:
        facts.append(f'  overlay: "{scene.overlay}" (code draws this on the finished ad)')
    return "\n".join(facts)


def starts_on(scene: Scene) -> str:
    """What the silent scene starts on: "real photo" or "ai scene"."""
    return str((scene.silent or {}).get("start", AI_SCENE))


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
        assert call.agent_fix is not None
        if starts_on(call.agent_fix.step.scene) == REAL_PHOTO:
            raise Refused("this scene starts on the real photo: call use_real_photo.")
        return make_picture(call, self.form, self.picks)


class UseRealPhoto(Tool):
    """Start this real-photo scene on the planned product photo itself: code fits it to the
    tall frame and keeps its pixels, cropped around focus_x, focus_y or with its background
    extended above and below. The picture check answers; you see the picture."""

    name: ClassVar[str] = "use_real_photo"
    focus_x: int = Field(
        default=50, ge=0, le=100, description="The product's centre: percent of width from left."
    )
    focus_y: int = Field(
        default=50, ge=0, le=100, description="The product's centre: percent of height from top."
    )

    def run(self, call: ToolCall) -> ToolResult:
        from . import current_run

        assert call.agent_fix is not None
        scene = call.agent_fix.step.scene
        window = current_run(call.agent_fix)
        old = scene.produced.filter(
            kind=ProducedItem.Kind.STARTING_PICTURE, sent__call=str(call.pk)
        ).first()
        if old is not None:
            return result(old)
        if starts_on(scene) != REAL_PHOTO:
            raise Refused("this scene is an AI scene: draw it with draw_silent_picture.")
        if window.starting_pictures >= window.starting_picture_limit:
            raise Refused(lifecycle.picture_tries_used_up(scene, window, "starting"))
        number = int((scene.silent or {})["photo"])
        item = next(
            (each for each in library_items.originals(scene.job) if each.number == number), None
        )
        if item is None:
            raise Refused(f"library item {number} is no longer in the library.")
        # A try that drew the bands before failing after is answered from its record.
        drawn = (
            ModelCall.objects.filter(
                tool_call=call, purpose="extend_real_photo", outcome=ModelCall.Outcome.SUCCEEDED
            )
            .order_by("created_at", "pk")
            .first()
        )
        try:
            fitted = real_photo.fit(
                scene.job,
                item.file,
                self.focus_x,
                self.focus_y,
                extended=str(drawn.output["file"]) if drawn and drawn.output else None,
            )
        except real_photo.PhotoNotFitted as error:
            raise _drawn_instead(scene, number, str(error)) from error
        except BlockedBySafetyFilter as error:
            notices.post_refused_by_the_filter(
                scene.job, f"library item {number}", "its background wasn't extended"
            )
            raise _drawn_instead(
                scene, number, "its background couldn't be extended around it"
            ) from error
        except real_photo.PixelsNotPasted as error:
            notices.post_notice(
                scene.job,
                notices.PIXELS_NOT_PASTED_BACK,
                f"Scene {scene.number}: library item {number}'s pixels couldn't be pasted back "
                f"over its extended background ({error}).",
                Message.Level.PROBLEM,
                scene=scene.number,
            )
            raise _drawn_instead(
                scene, number, "its background couldn't be extended around it"
            ) from error
        photos = [
            SelectedPhoto(
                file=item.file,
                line="Image 1: the shop's own product photo",
                library_number=item.number,
                original_file=item.file,
            )
        ]
        colour = cut_and_send.planned_colour(scene)
        ruling = check(
            scene.job,
            fitted.file,
            photos,
            scene=scene.number,
            colour=colour,
            starting=scene,
            making="starting picture",
        )
        made = keep_picture(
            call,
            fitted.file,
            prompt=real_photo.EXTEND_PROMPT if fitted.how == "extended" else "",
            ruling=ruling,
            photos=photos,
            colour=colour,
            form={
                "real_photo": number,
                "how": fitted.how,
                "focus_x": self.focus_x,
                "focus_y": self.focus_y,
            },
            previous_picture=current_picture(call),
        )
        return result(made)


def _drawn_instead(scene: Scene, photo: int, why: str) -> Refused:
    """The real photo can't start the scene: it becomes an AI scene, the owner is told, and the
    agent is told to draw it."""
    Scene.objects.filter(pk=scene.pk).update(silent={**(scene.silent or {}), "start": AI_SCENE})
    notices.post_notice(
        scene.job,
        notices.REAL_PHOTO_NOT_USED,
        f"Scene {scene.number} couldn't start on your photo as it is ({why}), so it starts on "
        "a picture drawn from your photos instead.",
        Message.Level.PROBLEM,
        scene=scene.number,
    )
    return Refused(
        f"library item {photo} can't start the scene: {why}. The scene is an AI scene now: "
        "draw it with draw_silent_picture."
    )


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
