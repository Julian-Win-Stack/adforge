"""Film a checked scene picture and return the clip judge's feedback."""

from typing import ClassVar

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from pydantic import Field

from agents import fixing_agent
from agents.loop import Refused, Tool, ToolResult
from agents.models import ToolCall
from agents.scene_forms import NOT_NEEDED, VideoForm, video_prompt
from chat.models import Message
from jobs import cut_and_send, library, notices, work
from jobs.models import AgentFix, AgentFixTry, Job, ProducedItem, Scene, SceneAgentRun, SceneStep

from .draw_starting_picture import current_picture
from .photos import SelectedPhoto
from .reference import pieces_for


class Film(Tool):
    """Film the current starting picture. Fill the video form after looking at the picture;
    read the clip's frames and judge's words, then fix the picture or film again as needed.
    """

    name: ClassVar[str] = "film"
    form: VideoForm
    closest_picture: int | None = Field(
        default=None,
        description="Only after all starting pictures failed: name the closest picture's ID.",
    )
    reason: str = Field(default="", description="Why the named failed picture is the closest.")

    def run(self, call: ToolCall) -> ToolResult:
        from . import current_run, finish

        assert call.agent_fix is not None
        fixing = call.agent_fix
        scene = fixing.step.scene
        row = fixing.tries.filter(tool_call=call).first()
        if row is None:
            window = current_run(fixing)
            picture = current_picture(call)
            if self.closest_picture is not None:
                failed = scene.produced.filter(
                    kind=ProducedItem.Kind.STARTING_PICTURE,
                    sent__run=window.pk,
                    ruling__status="failed",
                )
                if window.pictures < 6 or failed.count() != window.pictures:
                    raise Refused(
                        "Choose a failed picture only after all six pictures failed the check."
                    )
                if not self.reason.strip():
                    raise Refused(
                        "Say why this failed picture is the closest to the product photos."
                    )
                picture = failed.filter(pk=self.closest_picture).first()
                if picture is None:
                    raise Refused("Name a failed starting picture from this scene's current run.")
            if (
                picture is None
                or (picture.ruling or {}).get("status")
                not in {
                    "passed",
                    "unchecked",
                }
                and self.closest_picture is None
            ):
                raise Refused("Film only after the current starting picture has passed its check.")
            snapshot = (picture.sent or {}).get("scene", {})
            if any(getattr(scene, field) != value for field, value in snapshot.items()):
                raise Refused("The starting picture is out of date; draw and check a new picture.")
            window = current_run(fixing)
            if window.clips >= 6:
                raise Refused("This run has finished filming its clips.")
            # Code's fixed lines are exempt; only the agent's form boxes are checked.
            boxes: list[str] = []
            for value in self.form.model_dump().values():
                boxes.extend(value if isinstance(value, list) else [value])
            bans = sorted(
                {
                    match.group(0)
                    for box in boxes
                    if not NOT_NEEDED.fullmatch(box)
                    for match in fixing_agent.BANNED.finditer(box)
                }
            )
            if bans:
                raise Refused(
                    f"The video form has bans ({', '.join(bans)}). Say what happens instead."
                )
            audio = (
                scene.produced.filter(kind=ProducedItem.Kind.LINE_AUDIO, step__line=scene.line)
                .order_by("-created_at", "-pk")
                .first()
            )
            if audio is None:
                raise Refused("The current line must be voiced and heard before filming.")
            pieces = pieces_for(call, self.form)
            prompt = video_prompt(self.form, reference_mode=bool(pieces))
            selected = [
                SelectedPhoto.model_validate(photo)
                for photo in (picture.sent or {}).get("photos", [])
            ]
            photos = [photo for photo in selected if photo.library_number is not None]
            sent = library.SentList(
                colour=cut_and_send.planned_colour(scene),
                pictures=[],
                pieces=pieces,
                left_out=[],
                recolours=[],
                prompt=prompt,
                mode="reference" if pieces else "start_frame",
                starting_picture=library.StartingPicture(file=picture.file),
                scene_run=window.pk,
                picture_photos=[
                    library.SentPicture(
                        image=number,
                        tier=2,
                        library_number=photo.library_number,
                        drawn=None,
                        for_=None,
                        label=photo.line,
                        file=photo.file,
                    )
                    for number, photo in enumerate(photos, 1)
                ],
            )
            with transaction.atomic():
                step = fixing_agent._try_step(
                    fixing,
                    call,
                    SceneStep.Kind.CLIP,
                    made_from=audio,
                    picture=picture,
                    motion_prompt=prompt,
                )
                row = AgentFixTry.objects.create(
                    agent_fix=fixing,
                    number=fixing.tries.count() + 1,
                    tool_call=call,
                    step=step,
                    picture=picture,
                    scene_row=fixing_agent._row(scene),
                    video_prompt=prompt,
                    sent=sent.model_dump(mode="json"),
                )
                SceneAgentRun.objects.filter(pk=window.pk).update(clips=window.clips + 1)
        assert row.step is not None and row.sent is not None
        step = row.step
        sent = library.SentList.model_validate(row.sent)
        assert sent.scene_run is not None
        window = SceneAgentRun.objects.get(pk=sent.scene_run)
        if row.clip is None:
            clip = step.produced.filter(kind=ProducedItem.Kind.CLIP).first() or work._make_clip(
                step, motion=row.video_prompt, sent=sent
            )
            ProducedItem.objects.filter(pk=clip.pk).update(
                remake=row.number, change=window.instruction
            )
            clip.refresh_from_db()
            if settings.QUALITY_CHECKS:
                work._judge_clip(step, clip)
                clip.refresh_from_db()
            row.clip = clip
            row.passed = fixing_agent._passes(row, clip)
            row.save(update_fields=["clip", "passed"])
            fixing_agent._done(step)
        if self.closest_picture is not None:
            # Tell the owner only once the clip exists, including after checkpoint recovery.
            text = (
                f"Scene {scene.number}'s picture never passed the check; we filmed the closest one."
            )
            with transaction.atomic():
                locked = Job.objects.select_for_update().get(pk=scene.job_id)
                if not any(
                    warning.get("kind") == notices.CLOSEST_PICTURE_FILMED.name
                    and warning.get("text") == text
                    and warning.get("at", "") >= window.created_at.isoformat()
                    for warning in locked.warnings
                ):
                    notices.post_notice(
                        locked, notices.CLOSEST_PICTURE_FILMED, text, Message.Level.PROBLEM
                    )
        if row.passed:
            with transaction.atomic():
                AgentFix.objects.filter(pk=fixing.pk).update(
                    passed=row.clip, finished_at=timezone.now()
                )
                SceneAgentRun.objects.filter(pk=window.pk).update(finished_at=timezone.now())
                Scene.objects.filter(pk=scene.pk).update(status=Scene.Status.FINISHED)
        elif window.clips >= 6:
            fixing.ended_by_cap = True
            fixing.save(update_fields=["ended_by_cap"])
            finish(fixing, fixing_agent.ENDED_BY_CAP)
        return fixing_agent._told(fixing, row)
