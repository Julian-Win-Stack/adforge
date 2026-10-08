"""The producer's plan: what it is handed, what it must hand back, and the rules it follows."""

from typing import Any, Literal, Self

from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator

from gateway.types import Handoff, Judgement

# How a B-roll scene's details are given, by the planner and by a rewrite alike.
BROLL_DETAILS_INSTRUCTIONS = """\
For each B-roll scene, also give:
- Its kind: "does a job" when the product does something you can see, such as a pan \
searing a steak or a cloth wiping a spill away; "showcase" for everything else, the \
product at its best. Choose "showcase" when unsure.
- Who is in it: "has face" when the presenter's face is in the scene, "no face" when it \
isn't. A hand or a body without a face is "no face". The only person ever shown is the \
presenter: never anyone else.
- Its usage: how the product is used in the scene, read from the page's "how to use" or a \
scene the shop owner approved, and plan the scene with it. Null if the product isn't used \
in it.
- For "does a job", its result: what you can see at the end, which the scene ends on, \
such as "the spill wiped away". Only a result the page states or the shop owner approved. \
Null for "showcase".
- Its needs: what the scene needs that the main photo can't show, such as what a gel \
looks like out of the tube, each with the numbers of the photos that show it. The main \
photo is one of the photos showing the product in its colour; leave needs empty when it's \
enough. A needed photo may be any of the photos, even one where the product isn't \
clearly seen. Never name a photo that shows the product in another colour than the \
ad's. A scene's clip is sent at most 5 pictures: the main photo, one for each need, \
and the presenter's portrait when it "has face".
"""

# What a B-roll scene films, for the planner and a rewrite alike. Graded #8 toilet (4
# steps in one clip) and #5 bag (strap on and off in one clip) failed; one step was perfect.
# No exception for steps that flow together: N5's clip jumped where Boreal skipped the middle.
BROLL_ONE_ACTION_INSTRUCTIONS = """\
A B-roll scene films one action: one movement, the one moment that proves what its line \
claims. When the page or the line lists steps for using the product, film only the main \
step, the one that shows it working, such as the cloth wiping a stain away rather than \
spraying, waiting and rinsing, and write that B-roll line about that step. This holds \
even for steps that flow into each other with no pause: the video skips the middle of a \
chain of movements, so film only the main one. Putting the product on or taking it off, \
attaching, fitting, adjusting or turning it is a movement of its own: a scene films it or \
what comes after it, never both.
"""

PLAN_INSTRUCTIONS = (
    """\
You are the producer of a short vertical video ad for one product. A person speaks to \
camera, one line per scene, and each scene shows the product. You get the product page's \
visible text, followed by any product data the page declares for search engines, the \
target length in seconds (or null if the shop owner didn't set one), how many product \
photos there are, and the conversation with the shop owner so far, each message labelled \
"user" for the shop owner or "producer" for you. After that come the product photos \
themselves, each labelled with its number: "Photo 1", "Photo 2".
Every claim in the ad must come from the page or the shop owner's own words. Your own \
messages are there only to show what was asked: never take a fact from them, except what \
a scene shows, how the product is used in it and its result when you proposed it and the \
shop owner then approved it: that, with any change they asked for, counts as their own \
words. The photos \
are only for the product's colour and how it looks: never take any other fact from them, \
such as text on a label. Never infer, guess or make anything up: not a price, a size, \
a material, a benefit or a colour.
Decide "plan" when you can plan the whole ad from what you have. Give the scenes in the \
order they play, with each scene's line exactly as the person will say it. The ad is \
about one idea: the product's main benefit. It plays in three parts: the hook, the body \
and the call to action; give each scene the part it plays. The hook is the first scene \
alone: one line that makes the viewer stop and watch, starting with the problem or a \
surprising claim, not the product's name. The call to action is the last scene alone: it \
says the price and tells the viewer to get it now, with a reason to act now, such as a \
discount, only if the page or the shop owner gives one; never "learn more". For the body, \
choose the one format that suits the product and give it as the script's format; never \
mix two. Its parts play in the order given, each in one scene or more:
- "problem, agitate, solve": the problem, agitate, solve: the problem named clearly, \
what it costs not to solve it, then the product as the answer. Best for products that fix \
a pain, health and wellness, and productivity.
- "feature cascade": the hero feature, a supporting feature, proof: the most impressive \
thing the product does, one or two more, then a quick demo of a result. Best for tech \
and products with many features.
- "before and after": the before state, the transformation moment, the after state: the \
problem shown, the product in action, then the better result. Best for beauty, fitness, \
home improvement and cleaning products.
- "day in the life": the routine, the key moment, the result: the product in everyday \
life, its main use, then the payoff. Best for lifestyle products.
A problem, a feature or a result is only one the page or the shop owner states: never \
make one up. With a target \
length, write only as many words as fit it when spoken at an easy pace. Say the \
product's name, as the page states it (often the brand or a short name), in at least one \
scene where the person talks to camera; it may be said in other scenes too. Give the \
product's name exactly as the person says it in one of those scenes, copied word for \
word from its line: not the page's full title, and nothing the person doesn't say, such \
as a part in brackets or a symbol like ® or ™. One line says the product's price: the \
price a buyer pays today, so on a sale, the sale price. No \
line names the product's colour: the ad shows the colour, never says it. Give a scene an \
overlay, a few words drawn along the top of the picture while it plays, when there is \
something worth showing as well as saying, such as the price or the product's name; \
otherwise set it to null. An overlay states only what the page or the shop owner states, \
like a line. Name the product's \
colour as the photos show it, in plain words such as "sage green", and give the numbers \
of the photos that show the product in that colour. Leave out any photo that shows \
another product next to this one, even one from the same brand or range, such as a \
line-up, a set, a routine or a comparison, and any photo where this product can't be \
clearly seen. If the product comes in several \
colours, don't ask which: pick the colour with the most photos where the product is \
clearly seen; on a tie, the colour of Photo 1, the first photo. Say how big the product is, judged \
from the photos you chose: "tiny" if it fits on a fingertip, such as earrings, earbuds or \
a ring; "handheld" if it's held in one or two hands, such as a bottle, a bag, a power bank \
or a rolled-up mat; "large" if it can't be held, such as a chair, a treadmill or a \
mattress. The size decides how the person is posed with the product in every scene where \
they talk to camera, so judge it by what a person could really hold. Describe the person \
who presents the ad: whether they are a man or a woman, how they look, for a portrait, \
and how their voice sounds, for a voice designed to match. The portrait and the voice are \
made separately from these words, and both are of the same person, so the looks and the \
voice must agree with the gender you set and with each other. Describe only the person \
and where they are: never the product, anything they hold, any animal, or what they do in \
a scene. Choose someone who suits the product and its buyers, and never a real, famous \
person. Set question to null.
Most scenes are the person talking to camera. A B-roll scene instead shows the product \
while the person's voice says the line over it: the product being used, what it does, \
or the proof. For a B-roll scene, set shows to what the scene shows, in plain words, such \
as "a hand pours the sauce over a bowl of noodles"; for a talking scene, set it to null. \
The first scene is always the person talking to camera. Use B-roll for lines about how \
the product is used, what it does, or proof, and only to show what the page or the shop \
owner states, or the photos show: a picture is a claim, just like a sentence. What a \
scene shows must match what its line says while it says it, so a B-roll line is about \
what is shown. A B-roll line has at least about 10 words.
"""
    + BROLL_ONE_ACTION_INSTRUCTIONS
    + """\
The person can name the other steps in a talking scene. A scene's result is what the \
camera sees when its one action ends. When a claim needs two end states, give each its \
own B-roll scene, back to back, each with its own line and its one action, and mark the \
second as the second state. A claim needs two end states in two cases. The product can be \
used in two different ways that can't be seen at once, such as a jacket worn on either \
side: film each way with the product already set up that way, never the change from one \
way to the other. Or the proof is seen only after the action ends, such as a dropped \
item turned over to show it's unharmed: give what's seen after it its own B-roll scene \
right after. Never more than two scenes for one claim. Never show before \
and after pictures of bodies or skin, a screen whose content you'd have to invent, a \
result the page doesn't state, or parts of the product no photo \
shows. How much of the ad is B-roll depends on the kind of product. As a guide (B-roll \
share; what it can show; only if the page says; never):
- Beauty and skincare: about 30%; a texture close-up (a dab on a fingertip), hands \
applying it, the pack; only if the page gives the texture and how it's applied; never \
skin before and after, skin problems or a visible result on skin.
- Food, drink and kitchen: about 50%; a pour, sizzle and steam, a cooking step, the \
finished dish; only foods or recipes the page names and what the cookware does; never \
other foods, half-eaten food or chewing.
- Fashion: about 40%; fabric close-up, the garment moving, a detail, worn and turning; \
only the material, fit and features the page gives; never fit or stretch it doesn't \
claim.
- Home and cleaning: about 50%; the product in a room, hands using it, setup, a surface \
before and after; only the result the page states; never a stronger result.
- Tech and gadgets: 50 to 60%; design close-up, the feature working in hands, an \
everyday setting, unboxing; only features and box contents the page lists; never \
invented screens or readable small print.
- Fitness equipment: about 40%; in use, folded away; only the exercise and size \
claims the page makes; never body changes.
- Supplements and health: about 20%, mostly talking; the routine, such as a scoop into a \
shaker; only how it's taken; never any body, weight or health outcome.
- Cars: about 50%; driving on a road, design details, the interior; only a car the page \
names; never performance it doesn't claim or other brands' cars.
- Car accessories: about 50%; fitted in a car, in use, an installation step; only cars \
and installing the page describes.
- Apps, software and services: none, unless the shop owner supplies the screens.
- Pets: about 50%; the pet using the product; only the animal and use the page names; \
never health outcomes.
- Kids, baby and toys: 40 to 50%; a toy mid-play with hands only, the gear's features on \
the product alone; only claims the page makes; never children.
- Jewelry: about 40%; an extreme close-up, worn on a hand or neck; only the material and \
stones the page gives; never detail the photos don't show.
You may go against the guide for an unusual product if your reason says why.
"""
    + BROLL_DETAILS_INSTRUCTIONS
    + """\
Decide "ask" when you don't know the one price to say, because neither the page nor the \
shop owner gives a price, or they give different prices to choose between (such as a \
single item, a pack and a subscription). Also decide "ask" when the page conflicts with \
itself or is missing something else the ad needs, so that planning would mean guessing. \
Also decide "ask" when no photo clearly shows the product: ask them to attach one. A \
photo where the product is small beside pictures of other products, such as a chart of the \
devices it works with, or half hidden by text, doesn't clearly show it.
Ask because of a scene that shows the product in only three cases, and nothing else:
- The scene needs something of the product that no photo shows and the video would \
have to guess, such as what a serum looks like out of the bottle or a gel coming out of \
its tube. Ordinary things around the product are never missing: a phone, a hand, a \
bowl of noodles can be shown without a photo. A photo that shows it, even one where the \
product isn't clearly seen, such as blush on a cheek, is enough.
- The product does a job you can see, such as a cleaner or a pan, and neither the page \
nor the shop owner says how it is used. Ask even if you could plan it only at its best: \
an ad for such a product shows it doing its job. A product that doesn't do a job you can \
see, such as a bag, needs no "how to use": never ask for one.
- The scene ends on a result, what the product removes or prevents is a thin \
film, haze, cloudiness or water spots on a surface such as glass, tile, a mirror or \
chrome, and no photo shows that surface before and after. Ask even when everyone knows the \
word: a camera barely sees a thin film, so the video would guess how it looks and how much \
changes. The page naming the result isn't enough: the video would still guess how it \
looks. When what it removes is a coloured mark anyone sees at a glance, such as a stain, a \
ring, rust, mud, grease or dirt, never ask: show that mark, even if the page also names a \
film.
Then say in plain words what's missing and offer two answers: attach a photo of it (for \
a missing "how to use", of the product being used; for a result, of it before and \
after), or go ahead without one, and you'll \
say how you'd show it instead. Never choose for them. If they go ahead without one, ask \
again: say in plain words how you'd show it with what you have, for them to approve or \
change. Their approval is their own words, and for a product that does a job, it gives \
the scene's usage and result. \
Ask the shop owner one short, specific question, and set plan to null.
Give one sentence saying why: for a plan, why this many scenes; for a question, why you \
need to ask. Write it for the shop owner. You may say what the ad would show, but never \
tell them which scenes show the product rather than the person talking, or that there are \
two kinds of scene."""
)


# An overlay longer than this would wrap into a second line, down towards the face.
MOST_OVERLAY_CHARACTERS = 30


# What a B-roll scene is: the product doing something you can see, or at its best.
BrollKind = Literal["does a job", "showcase"]

# Whether a B-roll scene shows the presenter's face. Only a face matters: a hand or a body
# never does, and the only face ever shown is the presenter's, from their portrait.
PersonShown = Literal["no face", "has face"]

# The most pictures a B-roll clip is sent, the presenter's portrait counted: the most
# Boreal-H3 takes for free.
MOST_PICTURES = 5


class Need(BaseModel):
    what: str = Field(description='What the scene needs, in a few words, such as "the gel".')
    photos: list[StrictInt] = Field(
        min_length=1, description="The numbers of the photos that show it."
    )

    @field_validator("what")
    @classmethod
    def _not_empty(cls, what: str) -> str:
        if not what.strip():
            raise ValueError("This can't be empty.")
        return what


class ScriptScene(BaseModel):
    """A scene's line and, for a B-roll scene, what it shows and its B-roll details, as the
    planner gives them and a rewrite gives them back."""

    line: str = Field(description="Exactly what the person says in this scene.")
    shows: str | None = Field(
        default=None,
        description="For a B-roll scene, what it shows while the person's voice says the "
        "line, in plain words. Null for a scene where the person talks to camera.",
    )
    broll_kind: BrollKind | None = Field(
        default=None,
        description='For a B-roll scene, "does a job" when the product does something you can '
        'see, or "showcase" for the product at its best; "showcase" when unsure. Null for a '
        "scene where the person talks to camera.",
    )
    person_shown: PersonShown | None = Field(
        default=None,
        description='For a B-roll scene, "has face" when the presenter\'s face is in it, or '
        '"no face" when it shows no face (a hand is fine). Null for a scene where the person '
        "talks to camera.",
    )
    usage: str | None = Field(
        default=None,
        description="For a B-roll scene, how the product is used in it, read from the page's "
        '"how to use"; null when it isn\'t used. Null for a scene where the person talks to '
        "camera.",
    )
    result: str | None = Field(
        default=None,
        description='For a "does a job" scene, the result you can see, which the scene ends '
        'on. Null for a "showcase" scene or a scene where the person talks to camera.',
    )
    needs: list[Need] = Field(
        default_factory=list,
        description="For a B-roll scene, what it needs that the main photo can't show, each "
        "with the numbers of the photos that show it. Empty when the main photo is enough, "
        "and for a scene where the person talks to camera.",
    )

    # A validator rather than min_length, which OpenAI's structured output doesn't accept.
    @field_validator("line")
    @classmethod
    def _says_something(cls, line: str) -> str:
        if not line.strip():
            raise ValueError("A scene's line can't be empty.")
        return line

    @field_validator("shows")
    @classmethod
    def _blank_is_talking(cls, shows: str | None) -> str | None:
        return " ".join(shows.split()) or None if shows is not None else None

    @field_validator("usage", "result")
    @classmethod
    def _blank_is_none(cls, text: str | None) -> str | None:
        return text if text is not None and text.strip() else None

    @model_validator(mode="after")
    def _labelled_as_its_kind(self) -> Self:
        return self.check_broll_details()

    def check_broll_details(self) -> Self:
        """The scene, if its B-roll details are given as its kind needs them."""
        if self.shows is None:
            if self.has_broll_details():
                raise ValueError(
                    "A scene where the person talks to camera has no B-roll kind, person, "
                    "usage, result or needs."
                )
            return self
        if self.broll_kind is None:
            raise ValueError('A B-roll scene needs its kind: "does a job" or "showcase".')
        if self.person_shown is None:
            raise ValueError('A B-roll scene needs who is in it: "no face" or "has face".')
        if self.broll_kind == "does a job":
            if self.usage is None:
                raise ValueError(
                    'A "does a job" scene needs its usage: how the product is used in it.'
                )
            if self.result is None:
                raise ValueError(
                    'A "does a job" scene needs its result: what you can see at its end.'
                )
        elif self.result is not None:
            raise ValueError('A "showcase" scene has no result.')
        return self

    def has_broll_details(self) -> bool:
        """Whether any of the B-roll details is given."""
        return any((self.broll_kind, self.person_shown, self.usage, self.result, self.needs))

    def broll_details(self) -> dict[str, Any]:
        """The B-roll details as a Scene stores them: blank for a talking scene."""
        return {
            "broll_kind": self.broll_kind or "",
            "person_shown": self.person_shown or "",
            "usage": self.usage or "",
            "result": self.result or "",
            "needs": [need.model_dump() for need in self.needs],
        }

    def pictures(self) -> int:
        """How many pictures the scene's clip is sent: the main photo, one for each need,
        and the presenter's portrait when the face is shown."""
        return 1 + len(self.needs) + (self.person_shown == "has face")

    def too_many_pictures(self, number: int) -> str | None:
        """Why scene `number` would send its clip too many pictures, or None."""
        if self.pictures() <= MOST_PICTURES:
            return None
        portrait = " and the presenter's portrait" if self.person_shown == "has face" else ""
        return (
            f"Scene {number} would send {self.pictures()} pictures (the main photo, one for "
            f"each of its {len(self.needs)} needs{portrait}): {MOST_PICTURES} at most."
        )


def photos_missing(scene: ScriptScene, photo_count: int) -> str | None:
    """Why a scene's needs name a photo the job doesn't have, or None."""
    return photo_missing([number for need in scene.needs for number in need.photos], photo_count)


def photo_missing(numbers: list[int], photo_count: int) -> str | None:
    """Why `numbers` name a photo the job doesn't have, or None."""
    for number in numbers:
        if not 1 <= number <= photo_count:
            return f"There's no photo {number}: the job has {photo_count}."
    return None


# The structure an ad's body plays in, between its hook and its call to action: Creatify's
# ad generator's body structures, "Match the structure to your goal". Its Social Proof Stack
# is left out: it needs customer quotes and numbers the page rarely states. Nothing in code
# checks a plan's parts against its format: a refused plan is paid for again, so add a check
# only if plans break it.
ScriptFormat = Literal[
    "problem, agitate, solve", "feature cascade", "before and after", "day in the life"
]


# The part of the script a scene plays: the hook first, the call to action last, and the
# parts of the script's format between them.
Part = Literal[
    "hook",
    "problem",
    "agitate",
    "solve",
    "hero feature",
    "supporting feature",
    "proof",
    "before state",
    "transformation moment",
    "after state",
    "routine",
    "key moment",
    "result",
    "call to action",
]


class PlannedScene(ScriptScene):
    part: Part | None = Field(
        default=None,
        description='The part of the script this scene plays: "hook" for the first scene, '
        '"call to action" for the last, and one of the parts of the script\'s format between.',
    )
    second_state: bool = Field(
        default=False,
        description="True for a B-roll scene showing the second of two end states one claim "
        "needs, the first being the B-roll scene just before it; otherwise false.",
    )
    overlay: str | None = Field(
        default=None,
        description="A few words drawn along the top of the picture while this scene plays, "
        f"such as the price, {MOST_OVERLAY_CHARACTERS} characters at most, or null for none.",
    )

    @field_validator("overlay")
    @classmethod
    def _a_few_words(cls, overlay: str | None) -> str | None:
        # Drawn in one band along the top: longer text would wrap down over the face.
        if overlay is not None and len(" ".join(overlay.split())) > MOST_OVERLAY_CHARACTERS:
            raise ValueError(
                f"An overlay is a few words: {MOST_OVERLAY_CHARACTERS} characters at most."
            )
        return overlay


# How big the product is. Code owns the pose for each size (jobs.scenes.POSES), so a
# chair is never asked for at chest height and studs are never lost on an open palm.
ProductSize = Literal["tiny", "handheld", "large"]

# What the person presents as. Code puts it into both the portrait's prompt and the
# voice's description; a plan that leaves it out is refused, so neither model is left to
# pick a gender at random and disagree with the other.
PersonGender = Literal["man", "woman"]


class Plan(BaseModel):
    scenes: list[PlannedScene] = Field(min_length=1)
    product_name: str = Field(
        description="The product's name copied word for word from a line where the person "
        "talks to camera, exactly as they say it there: not the page's full title."
    )
    product_colour: str = Field(
        description='The product\'s colour as the photos show it, such as "sage green".'
    )
    colour_photos: list[StrictInt] = Field(
        min_length=1, description="The numbers of the photos showing the product in that colour."
    )
    product_size: ProductSize = Field(
        description='How big the product is: "tiny" fits on a fingertip, "handheld" is held '
        'in one or two hands, "large" can\'t be held.'
    )
    person_gender: PersonGender = Field(
        description="Whether the person presenting the ad is a man or a woman: the portrait "
        "and the voice both present as this."
    )
    person_looks: str = Field(
        description=(
            "How the person presenting the ad looks: age, style, clothes, setting. Only the "
            "person: no product, props or animals."
        )
    )
    person_voice: str = Field(description="How the person's voice sounds: age, accent, tone, pace.")
    script_format: ScriptFormat | None = Field(
        default=None,
        description="The one format the ad's body plays in, between its hook and its call to "
        "action.",
    )

    @field_validator("product_name", "product_colour", "person_looks", "person_voice")
    @classmethod
    def _not_empty(cls, text: str) -> str:
        if not text.strip():
            raise ValueError("This can't be empty.")
        return text

    @model_validator(mode="after")
    def _opens_on_the_person(self) -> Self:
        if self.scenes[0].shows is not None:
            raise ValueError("The first scene is the person talking to camera: its shows is null.")
        return self

    @model_validator(mode="after")
    def _five_pictures_at_most(self) -> Self:
        for number, scene in enumerate(self.scenes, start=1):
            if (too_many := scene.too_many_pictures(number)) is not None:
                raise ValueError(too_many)
        return self

    @model_validator(mode="after")
    def _second_state_after_the_first(self) -> Self:
        # The two end states of one claim play back to back, so the second's clip can be made
        # from the first's (audit row 11).
        for number, scene in enumerate(self.scenes[1:], start=2):
            if not scene.second_state:
                continue
            if scene.shows is None:
                raise ValueError(
                    f"Scene {number} is said to camera: only a B-roll scene shows a second state "
                    "of use."
                )
            first = self.scenes[number - 2]
            if first.shows is None:
                raise ValueError(
                    f"Scene {number} shows a second state, so scene {number - 1} must be "
                    "B-roll showing the first."
                )
            if first.second_state:
                raise ValueError(
                    f"Scene {number} shows a third state: one claim has two B-roll scenes at most."
                )
        return self

    @model_validator(mode="after")
    def _name_said_on_camera(self) -> Self:
        name = " ".join(self.product_name.split()).casefold()
        if not any(
            scene.shows is None and name in " ".join(scene.line.split()).casefold()
            for scene in self.scenes
        ):
            raise ValueError(
                f'No scene where the person talks to camera says "{self.product_name}": at '
                "least one must."
            )
        return self


class ProducerDecision(Judgement):
    decision: Literal["plan", "ask"]
    question: str | None = Field(description='The question for the shop owner, if "ask".')
    plan: Plan | None = Field(description='The plan, if "plan".')

    @model_validator(mode="after")
    def _plan_or_question(self) -> Self:
        if self.decision == "plan" and (self.plan is None or self.question is not None):
            raise ValueError('A "plan" decision needs a plan and no question.')
        if self.decision == "ask" and (not self.question or self.plan is not None):
            raise ValueError('An "ask" decision needs a question and no plan.')
        return self


def producer_decision_for(photo_count: int) -> type[ProducerDecision]:
    """The producer's decision for a job with `photo_count` photos. A plan naming a photo
    the job doesn't have, for its colour or for what a scene needs, fails while the answer
    is read, like any other broken plan."""

    class ProducerDecisionForJob(ProducerDecision):
        @model_validator(mode="after")
        def _real_photos(self) -> Self:
            if self.plan is None:
                return self
            for missing in (
                photo_missing(self.plan.colour_photos, photo_count),
                *(photos_missing(scene, photo_count) for scene in self.plan.scenes),
            ):
                if missing is not None:
                    raise ValueError(missing)
            return self

    return ProducerDecisionForJob


class ChatMessage(Handoff):
    """One message in the conversation with the shop owner: theirs ("user"), or the
    producer's."""

    by: Literal["user", "producer"]
    text: str


class PlanHandoff(Handoff):
    product_url: str
    page_text: str
    target_seconds: int | None
    photo_count: int
    conversation: list[ChatMessage]
