"""The producer's tool for planning a product-shots ad (spec #767, #770): it writes the
silent scenes itself, from the page and the photos it was shown (ADR 0016)."""

from typing import Literal

from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from jobs.library import OwnerPhoto, PagePhoto
from jobs.library_items import originals
from jobs.models import AdStyle, Job, ProducedItem, Scene, SceneStep
from jobs.pace import LONGEST_CLIP_SECONDS
from jobs.plan_edits import _move_chat_scenes
from jobs.planning import END_CARD_SECONDS
from jobs.product_shots import is_product_shots

from .loop import Refused, Tool
from .models import ToolCall
from .scene_agent.silent import Start
from .scene_forms import CameraMove

# The most silent scenes in one plan.
MOST_SCENES = 10


class SilentScenePlan(BaseModel):
    """One silent scene: no presenter and no spoken line."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    shows: str = Field(min_length=1, description="What the scene shows, in plain words.")
    photo: int = Field(description="The library number of the product photo the scene starts from.")
    start: Start = Field(
        description='"real photo": the scene starts on the shop\'s own photo itself, its '
        'pixels kept, with a slow camera move over it. "ai scene": a new picture drawn from '
        "the photos, in a setting the photos don't show."
    )
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


# The style labels the shop owner reads, by the producer's word for each.
STYLES = {"product shots": AdStyle.PRODUCT_SHOTS, "person talking": AdStyle.PERSON_TALKING}


class ChooseAdStyle(Tool):
    """Choose the ad's style after the page is read, from the buyer's doubt: name the 2 or 3
    doubts a first-time buyer has about this product, pick the one that matters most, and
    take its style. A style the shop owner asked for in any message is the style. Kept for
    the developer, never shown to the owner. Choosing a new style after the ad was planned
    takes the old plan out, to be planned again."""

    name = "choose_ad_style"

    doubts: list[str] = Field(
        min_length=2,
        max_length=3,
        description="The 2 or 3 doubts a first-time buyer has about this product, each in "
        'the buyer\'s words, such as "is it beautiful enough to want?".',
    )
    chosen_doubt: str = Field(description="The doubt that matters most, one of `doubts`.")
    style: Literal["product shots", "person talking"] = Field(
        description='"product shots" answers "is it beautiful, do I want it?"; "person '
        'talking" answers "can I trust it, is it worth it?".'
    )
    reason: str = Field(min_length=1, description="One sentence, for the shop owner, on why.")
    owner_asked: bool = Field(
        description="True when the shop owner asked for this style in their own words."
    )

    def run(self, call: ToolCall) -> str:
        from .producer import _with_its_page

        job = _with_its_page(call, "the style is chosen from the page")
        if self.chosen_doubt not in self.doubts:
            raise Refused("chosen_doubt must be one of the doubts you named, word for word.")
        style = STYLES[self.style]
        carded = ((job.chosen_plan or {}).get("plan") or {}).get("style")
        if carded and carded != style and not self.owner_asked:
            raise Refused(
                f'the shop owner picked a "{AdStyle(carded).label}" plan on the plan card, so '
                "that is the style unless they ask for another."
            )
        pinned = call.session.pinned_ad_style
        if pinned and style != pinned:
            raise Refused(f'this chat makes "{AdStyle(pinned).label}" ads only. Choose that style.')
        changed = job.ad_style != style
        planned = changed and job.scenes.exists()
        with transaction.atomic():
            if planned:
                take_out_the_scenes(job)
            job.ad_style = style
            if changed:
                # The plan of the other style goes with it; the owner's own target stays.
                job.shots_plan = {}
            job.style_choice = {
                "doubts": self.doubts,
                "chosen": self.chosen_doubt,
                "style": style,
                "why": " ".join(self.reason.split()),
                "owner_asked": self.owner_asked,
            }
            job.save(update_fields=["ad_style", "shots_plan", "style_choice"])
        label = AdStyle(style).label
        plan_with = "plan_product_shots" if style == AdStyle.PRODUCT_SHOTS else "plan_ad"
        again = " The earlier plan was taken out." if planned else ""
        return (
            f'The ad style is "{label}".{again} Tell the shop owner in one short sentence: '
            f'"{label}", and why, then plan it with {plan_with} without waiting.'
        )


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
        if not is_product_shots(job):
            raise Refused('choose the "Product shots" style with choose_ad_style first.')
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
            job.product_colour = (self.product_colour.strip().lower().split() or ["none"])[0]
            job.product_photos = list(dict.fromkeys(scene.photo for scene in self.scenes))
            job.end_card_line = " ".join(self.end_card_line.split())
            job.end_card_price = self.end_card_price.strip()
            job.status = Job.Status.PLANNED
            job.save(
                update_fields=[
                    "shots_plan",
                    "product_name",
                    "product_colour",
                    "product_photos",
                    "end_card_line",
                    "end_card_price",
                    "status",
                ]
            )
        listed = "\n".join(
            f"{number}. {scene.seconds:g} s, {scene.start} from library item {scene.photo}, "
            f"{scene.move}: "
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


class MakeUpAPerson(Tool):
    """Only when the shop owner asks for a made-up person in place of the model in their
    photos, in a product-shots ad: make that person's portrait, and hand back the scenes
    whose photo shows the model. Pass a note to only those scenes, with the owner's words:
    the made-up person's face is swapped in for the model's. Other scenes stay."""

    name = "make_up_a_person"

    gender: Literal["man", "woman"] = Field(description="What the made-up person presents as.")
    looks: str = Field(
        min_length=1,
        description="How the made-up person looks, in a few words, fitting the product and "
        "anything the owner said.",
    )
    quotes: list[str] = Field(
        min_length=1, description="The shop owner's exact words asking for a made-up person."
    )

    def run(self, call: ToolCall) -> str:
        from chat import messages
        from chat.models import Attachment, Message
        from jobs import face_swap, work

        from .owner_quotes import QuoteNotFound, check_session_quotes
        from .producer import _shown_before, _the_job

        try:
            check_session_quotes(call.session, self.quotes)
        except QuoteNotFound as error:
            raise Refused(str(error)) from error
        job = _the_job(call)
        if job is None or not is_product_shots(job) or not job.scenes.exists():
            raise Refused(
                "a made-up person replaces the shop's model in a planned product-shots ad; a "
                "person-talking ad has its presenter already."
            )
        if job.made_up_person:
            raise Refused(
                "the made-up person is made already; to change how they look, use "
                "remake_person, then pass_note to the scenes that show them."
            )
        job.person_gender = self.gender
        job.person_looks = " ".join(self.looks.split())
        job.made_up_person = True
        job.save(update_fields=["person_gender", "person_looks", "made_up_person"])
        # A presenter left from a person-talking plan isn't the person the owner asked for:
        # a new portrait is drawn from these looks.
        portrait = (
            work.create_person(job)
            if work.latest(job, ProducedItem.Kind.PORTRAIT) is None
            else work.remake_person(
                job, look=True, instruction=f"The person looks {job.person_looks}.", call=call
            )
        )
        assert portrait is not None
        if not _shown_before(call.session, portrait.file):
            messages.add(
                call.session,
                role=Message.Role.AGENT,
                carrying=[messages.AttachedFile(Attachment.Kind.PICTURE, portrait.file)],
            )
        photos = {item.number: item for item in originals(job)}
        showing = [
            scene.number
            for scene in job.scenes.order_by("number")
            if any(
                face_swap.shows_a_face(job, photos[number])
                for number in _photos_of(scene)
                if number in photos
            )
        ]
        if not showing:
            return (
                "The made-up person is made and their portrait is shown, but no scene shows a "
                "photo with the model's face, so nothing is made again. Tell the owner."
            )
        listed = ", ".join(map(str, showing))
        which = f"Scene {listed} shows" if len(showing) == 1 else f"Scenes {listed} show"
        return (
            "The made-up person is made and their portrait is shown. "
            f"{which} a photo with the model's face: pass_note to only those scenes, "
            "with the owner's words, and the made-up person is swapped in. Tell the "
            "owner which scenes are made again."
        )


def _photos_of(scene: Scene) -> set[int]:
    """The library items `scene`'s current starting picture was made from, or its planned
    photo when it has none yet."""
    picture = scene.produced.filter(kind=ProducedItem.Kind.STARTING_PICTURE).order_by("-pk").first()
    used = {
        int(photo["library_number"])
        for photo in ((picture.sent or {}).get("photos", []) if picture else [])
        if photo.get("library_number") is not None
    }
    return used or {int((scene.silent or {}).get("photo", 0))}
