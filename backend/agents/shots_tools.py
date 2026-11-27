"""The producer's tool for planning a product-shots ad (spec #767, #770): it writes the
silent scenes itself, from the page and the photos it was shown (ADR 0016)."""

from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from jobs.library import OwnerPhoto, PagePhoto
from jobs.library_items import originals
from jobs.models import AdStyle, Job, Scene, SceneStep
from jobs.pace import LONGEST_CLIP_SECONDS
from jobs.plan_edits import _move_chat_scenes
from jobs.planning import END_CARD_SECONDS

from .loop import Refused, Tool
from .models import ToolCall
from .scene_forms import CameraMove

# The most silent scenes in one plan.
MOST_SCENES = 10


class SilentScenePlan(BaseModel):
    """One silent scene: no presenter and no spoken line."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    shows: str = Field(min_length=1, description="What the scene shows, in plain words.")
    photo: int = Field(description="The library number of the product photo the scene starts from.")
    move: CameraMove = Field(description="The slow camera move.")
    overlay: str = Field(
        description="The few words drawn on the scene, in the page's or the shop owner's own "
        'words, or "" for none.'
    )
    seconds: float = Field(
        description=f"How long the scene plays, from 1 to {LONGEST_CLIP_SECONDS} seconds."
    )


def take_out_the_scenes(job: Job) -> None:
    """Take every scene out of the plan, keeping what was made and paid for, so the ad is
    planned again. Refused while any scene's step is being made: its agent would keep paying
    for a scene no longer in the plan. Run inside the caller's transaction."""
    Job.objects.select_for_update().get(pk=job.pk)
    running = SceneStep.objects.filter(
        scene__job=job, scene__removed_at__isnull=True, status=SceneStep.Status.RUNNING
    ).first()
    if running is not None:
        raise Refused(
            f"scene {running.scene.number} is still being made. You'll be told when it's "
            "ready; plan the ad again then."
        )
    numbers = list(job.scenes.values_list("number", flat=True))
    job.scenes.update(removed_at=timezone.now())
    # Their chat files are no longer a scene's: the new plan's scenes take their numbers.
    _move_chat_scenes(job, dict.fromkeys(numbers))


class PlanProductShots(Tool):
    """Plan a product-shots ad yourself, when it is the ad's style: silent scenes of the
    product with no presenter and no spoken line; code draws the overlays, the music plays
    alone, and the end card comes last. Build the story on the listing's most unusual selling
    point. Starting point: about 15 seconds with the end card (2 s), 3 or 4 scenes, the scene
    that shows the product the longest; plan more or fewer, longer or shorter, when the
    product calls for it or the shop owner asks (30 seconds when asked). Called again, the new
    plan replaces the old one, and the ad is made again from it."""

    name = "plan_product_shots"
    stops_ad_when_filter_blocks = True
    waits_for_the_plan_card = True

    story: str = Field(min_length=1, description="The ad's story in one sentence.")
    built_on: str = Field(
        min_length=1, description="The listing's most unusual selling point it is built on."
    )
    scenes: list[SilentScenePlan] = Field(description="The scenes, in the order they play.")
    music_mood: str = Field(
        min_length=1, description='The music\'s mood in a few words, such as "calm piano".'
    )
    product_name: str = Field(min_length=1, description="The product's name for the end card.")
    product_colour: str = Field(
        description='The product\'s colour in one word, from the page, or "none".'
    )
    end_card_line: str = Field(description="One line for the end card, in the page's words.")
    end_card_price: str = Field(
        description='The price for the end card as the page gives it, or "" for none.'
    )

    def run(self, call: ToolCall) -> str:
        from .producer import _with_its_page

        job = _with_its_page(call, "a product-shots ad is planned from the page")
        if not 1 <= len(self.scenes) <= MOST_SCENES:
            raise Refused(f"plan from 1 to {MOST_SCENES} scenes.")
        photos = sorted(
            item.number
            for item in originals(job)
            if isinstance(item.source, (PagePhoto, OwnerPhoto))
        )
        wrong = sorted({scene.photo for scene in self.scenes} - set(photos))
        if wrong:
            raise Refused(
                f"library item {', '.join(map(str, wrong))} isn't a product photo; the "
                f"product photos are {', '.join(map(str, photos))}."
            )
        for number, scene in enumerate(self.scenes, 1):
            if not 1 <= scene.seconds <= LONGEST_CLIP_SECONDS:
                raise Refused(
                    f"scene {number} lasts {scene.seconds:g} seconds: a scene lasts from 1 to "
                    f"{LONGEST_CLIP_SECONDS} seconds."
                )
        total = sum(scene.seconds for scene in self.scenes) + END_CARD_SECONDS
        with transaction.atomic():
            take_out_the_scenes(job)
            Scene.objects.bulk_create(
                Scene(
                    job=job,
                    number=number,
                    line="",
                    kind=Scene.Kind.SILENT,
                    shows=" ".join(scene.shows.split()),
                    overlay=" ".join(scene.overlay.split()),
                    silent={
                        "photo": scene.photo,
                        "move": scene.move,
                        "seconds": round(scene.seconds, 2),
                    },
                )
                for number, scene in enumerate(self.scenes, 1)
            )
            job.ad_style = AdStyle.PRODUCT_SHOTS
            job.shots_plan = {
                "story": self.story.strip(),
                "built_on": self.built_on.strip(),
                "music_mood": self.music_mood.strip(),
            }
            job.product_name = self.product_name.strip()
            job.product_colour = (self.product_colour.strip().lower().split() or ["none"])[0]
            job.product_photos = list(dict.fromkeys(scene.photo for scene in self.scenes))
            job.end_card_line = " ".join(self.end_card_line.split())
            job.end_card_price = self.end_card_price.strip()
            job.target_seconds = round(total)
            job.status = Job.Status.PLANNED
            job.save(
                update_fields=[
                    "ad_style",
                    "shots_plan",
                    "product_name",
                    "product_colour",
                    "product_photos",
                    "end_card_line",
                    "end_card_price",
                    "target_seconds",
                    "status",
                ]
            )
        listed = "\n".join(
            f"{number}. {scene.seconds:g} s from library item {scene.photo}, {scene.move}: "
            f"{scene.shows}" + (f' Overlay: "{scene.overlay}".' if scene.overlay else "")
            for number, scene in enumerate(self.scenes, 1)
        )
        return (
            f"Planned a product-shots ad of {len(self.scenes)} silent scenes, {total:g} seconds "
            f"with the end card. Story: {self.story}\n{listed}\nNo person is made for it. "
            "Next: run_planning_checks (it checks the overlays and the end card against the "
            f'page), create_music in the mood "{self.music_mood}", then start_scene_agent for '
            "every scene together."
        )
