"""The producer's tools for the ad style (#774) and for planning a product-shots ad (#770):
the style chosen from the buyer's doubt, and the silent scenes the producer writes itself."""

from typing import Literal

from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from chat.models import Message
from jobs import notices, product_shots
from jobs.library import OwnerPhoto, PagePhoto
from jobs.library_items import originals
from jobs.models import Job, ProducedItem, Scene
from jobs.notices import post_notice

from .loop import Refused, Tool
from .models import ToolCall

Style = Literal["product shots", "person talking"]
STYLE_OF = {
    "product shots": product_shots.PRODUCT_SHOTS,
    "person talking": product_shots.PERSON_TALKING,
}
# A product-shots plan's length in seconds, end card included, may be at most this.
LONGEST_AD = 62


def _job(call: ToolCall) -> Job:
    from .producer import _with_its_page

    return _with_its_page(call, "the ad's style and plan come from the page")


def _filmed(job: Job) -> bool:
    return job.produced.filter(kind=ProducedItem.Kind.CLIP, scene__removed_at=None).exists()


def _clear_scenes(job: Job) -> None:
    """Take every scene out of the plan, keeping what was made and paid for: a new style or
    a new plan of silent scenes plans the ad again."""
    job.scenes.update(removed_at=timezone.now())


class ChooseAdStyle(Tool):
    """Choose the ad's style from the buyer's doubt, after the page is read: name the 2 or 3
    doubts a first-time buyer has about this product, pick the one that matters most, and
    take its style. The shop owner's own words win: a style they asked for is the style.
    Kept with the job for the developer; tell the owner the style and why in one sentence.
    Choosing a new style after the ad was planned plans it again."""

    name = "choose_ad_style"

    doubts: list[str] = Field(
        min_length=1,
        max_length=3,
        description="The 2 or 3 doubts a first-time buyer has about this product, each in the "
        'buyer\'s words, such as "is it beautiful enough to want?".',
    )
    chosen_doubt: str = Field(description="The doubt that matters most, one of `doubts`.")
    style: Style = Field(
        description='"product shots" answers "is it beautiful, do I want it?"; "person '
        'talking" answers "can I trust it, is it worth it?". The shop owner\'s asked style wins.'
    )
    reason: str = Field(description="One sentence, for the shop owner, on why this style.")
    owner_asked: bool = Field(
        description="True when the shop owner asked for this style in their own words."
    )

    def run(self, call: ToolCall) -> str:
        job = _job(call)
        style = STYLE_OF[self.style]
        changed = job.ad_style != style
        with transaction.atomic():
            if changed and job.scenes.exists():
                _clear_scenes(job)
            job.ad_style = style
            job.style_doubts = {
                "doubts": self.doubts,
                "chosen": self.chosen_doubt,
                "style": self.style,
                "why": self.reason,
                "owner_asked": self.owner_asked,
            }
            job.save(update_fields=["ad_style", "style_doubts"])
        label = product_shots.STYLE_LABELS[style]
        then = (
            "Plan it with plan_product_shots."
            if style == product_shots.PRODUCT_SHOTS
            else "Plan it with plan_ad."
        )
        cleared = " The earlier plan was taken out: plan the ad again." if changed else ""
        return (
            f'The ad style is "{label}".{cleared} Tell the shop owner in one short sentence: '
            f'"{label}", and why. Never use any other word for the style. {then}'
        )


class SilentScenePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    shows: str = Field(description="What the scene shows, in plain words.")
    photo: int = Field(description="The library number of the product photo it starts on.")
    start: Literal["real photo", "ai scene"] = Field(
        description='"real photo": the shop\'s own photo with a slow camera move, its pixels '
        "kept; use it wherever the buyer must trust what they see (the exact product, its "
        'size beside something known, the product worn in the shop\'s photo). "ai scene": a '
        "scene the page has no photo for, drawn from the product photos."
    )
    move: Literal["push in", "pull back"] = Field(description="The slow camera move.")
    overlay: str = Field(
        description="The few words drawn on this scene, from the page's or the owner's own "
        'words, or "" for none.'
    )
    seconds: float = Field(description="How long the scene plays, in seconds.")


class PlanProductShots(Tool):
    """Plan a product-shots ad yourself: silent scenes of the product with no presenter and
    no spoken line, overlays drawn by code, music only, the end card last. Build the story on
    the listing's most unusual selling point. Starting point: about 15 seconds with the end
    card (2 s), 3 or 4 scenes, the scene that shows the product the longest; plan more or
    fewer, longer or shorter, when the product calls for it or the owner asks (30 seconds
    when asked). Calling it again before any scene is filmed replaces the plan."""

    name = "plan_product_shots"
    stops_ad_when_filter_blocks = True

    story: str = Field(description="The ad's story in one sentence.")
    built_on: str = Field(description="The listing's most unusual selling point it is built on.")
    scenes: list[SilentScenePlan] = Field(min_length=1, max_length=8)
    music_mood: str = Field(description='The music\'s mood in a few words, such as "calm piano".')
    product_name: str = Field(description="The product's name as the end card says it.")
    product_colour: str = Field(
        description="The product's colour in one word, from the page (or none)."
    )
    end_card_line: str = Field(description="One line for the end card, in the page's own words.")
    end_card_price: str = Field(
        description='The price for the end card as the page gives it, or "" for none.'
    )

    def run(self, call: ToolCall) -> str:
        job = _job(call)
        if job.ad_style != product_shots.PRODUCT_SHOTS:
            raise Refused(
                "the ad's style isn't \"product shots\": choose it with choose_ad_style first."
            )
        if _filmed(job):
            raise Refused(
                "scenes of the ad are filmed already: change a scene with edit_scene or "
                "pass_note, or choose the style again to plan it from scratch."
            )
        photos = {
            item.number
            for item in originals(job)
            if isinstance(item.source, (PagePhoto, OwnerPhoto))
        }
        wrong = sorted({scene.photo for scene in self.scenes} - photos)
        if wrong:
            raise Refused(
                f"library item {', '.join(map(str, wrong))} isn't a product photo; the product "
                f"photos are {', '.join(map(str, sorted(photos)))}."
            )
        short = [n for n, scene in enumerate(self.scenes, 1) if not 1.0 <= scene.seconds <= 10]
        if short:
            raise Refused(f"scene {short[0]}'s length must be from 1 to 10 seconds.")
        total = sum(scene.seconds for scene in self.scenes) + 2
        if total > LONGEST_AD:
            raise Refused(f"the ad would last {total:g} seconds, over {LONGEST_AD}.")
        colour = (self.product_colour.strip().lower().split() or ["none"])[0]
        with transaction.atomic():
            _clear_scenes(job)
            Scene.objects.bulk_create(
                Scene(
                    job=job,
                    number=number,
                    line="",
                    kind=Scene.Kind.SILENT,
                    shows=scene.shows.strip(),
                    overlay=" ".join(scene.overlay.split()),
                    silent={
                        "photo": scene.photo,
                        "start": scene.start,
                        "move": scene.move,
                        "seconds": round(scene.seconds, 2),
                    },
                )
                for number, scene in enumerate(self.scenes, 1)
            )
            job.shots_plan = {
                "story": self.story.strip(),
                "built_on": self.built_on.strip(),
                "music_mood": self.music_mood.strip(),
            }
            job.product_name = self.product_name.strip()
            job.product_colour = colour
            job.product_photos = list(dict.fromkeys(scene.photo for scene in self.scenes))
            job.end_card_line = " ".join(self.end_card_line.split())
            job.end_card_price = self.end_card_price.strip()
            job.target_seconds = round(total)
            job.status = Job.Status.PLANNED
            job.save(
                update_fields=[
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
        if not job.photos.filter(from_video=None).exists():
            post_notice(
                job, notices.NO_PRODUCT_PHOTO, "I need a product photo.", Message.Level.INFO
            )
        listed = "\n".join(
            f"{number}. {scene.seconds:g} s, {scene.start} from library item {scene.photo}, "
            f"{scene.move}: {scene.shows}"
            + (f' Overlay: "{scene.overlay}"' if scene.overlay else "")
            for number, scene in enumerate(self.scenes, 1)
        )
        return (
            f"Planned a product-shots ad of {len(self.scenes)} silent scenes, {total:g} seconds "
            f"with the end card. Story: {self.story}\n{listed}\nNo person is made for it. Next: "
            "run_planning_checks (it checks the overlays and end card against the page), "
            f'create_music in the mood "{self.music_mood}", then start_scene_agent for every '
            "scene together."
        )
