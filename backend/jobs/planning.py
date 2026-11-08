"""The producer's plan: what it is handed, what it must hand back, and the rules it follows."""

import re
from collections.abc import Callable, Collection, Iterable, Mapping
from typing import Any, Literal, Self, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    create_model,
    field_validator,
    model_validator,
)

from gateway.types import LEAST_BROLL_SECONDS, Handoff, Judgement

from .library import For
from .pace import (
    FIXED_WORDS_PER_SECOND,
    HOOK_POINT_SECONDS,
    LONGEST_BROLL_LINE_SECONDS,
    SHORTEST_BROLL_LINE_SECONDS,
    asks_for_a_slow_pace,
    fewest_words_in_a_broll_line,
    most_words_in_a_broll_line,
    most_words_in_the_shortest_clip,
    typical_words_in_a_line,
    words_said_in,
)
from .picture_rules import LABEL_RULE, PLAN_OPEN_RULE
from .plan_rules import (
    AT_LEAST_ONE_BROLL,
    BOX_NEEDS_PACKAGING,
    DIFFERENT_STYLE_AND_STATE,
    LENS_FIRST_OR_LAST,
    LENS_ONLY,
    MIDDLE_IS_BROLL,
    STYLE_AND_STATE,
    PlanScene,
    ProductState,
    TalkingStyle,
    rule_broken,
    styles_said,
)
from .planning_questions import QuestionKind, default_for
from .reference_photos import same_thing

# The proven B-roll rule (docs/agents/proven-broll-rules.md), replacing "B-roll must sell:
# every clip does something" (Julian's OK, Nov 6 06:14, spec #401): told to the planner and the
# scene agent, and asked by the plan quality check (jobs/plan_quality.py), in these words.
# Price, offers and results over days need no proof (Julian's OK, Nov 6 18:16).
PROOF_RULE = (
    "Every B-roll scene shows proof of its line; a still product shot passes only when the "
    "line is about exactly that, such as where it fits or how it looks. A price, a discount, "
    "an offer or a call to buy needs no proof, and neither does a result that takes days to "
    "see, such as how long it lasts: prove the rest of the line."
)

# What a scene's written proof is, told to the planner and in the refusal of a plan with a
# scene that has none.
PROOF_IS = "what the viewer sees in the scene that proves its line"
# The planner's brand rule, in the words its refusal repeats (#423): a page whose text never
# writes its brand, such as one with the brand only in its link, plans with no brand.
BRAND_RULE = (
    "Give the brand exactly as the page text writes it. If the page text never writes a "
    "brand, leave brand empty."
)

# What a B-roll result may be (spec #401; Julian, Nov 6: the Oct 10 limitation is gone).
RESULT_SHOWN = (
    "A B-roll scene may show a result the page states in words, as the action's own result "
    "in the same clip, where the product touched; never a number shown as a result, and "
    "never a before and after over days."
)

# How a B-roll scene's details are given, by the planner and by a rewrite alike.
BROLL_PLAN_DETAILS_INSTRUCTIONS = (
    """\
For each B-roll scene, also give:
- Its kind: "does a job" when the product does something you can see, such as a pan \
searing a steak or a cloth wiping a spill away; "shows the problem" when the line names \
the problem the product fixes, as the page names it: the problem as it really is before \
the product is used, with the product in view. Only a problem anyone sees at a glance, or \
one a photo shows. Never the problem getting worse or building up over time: show it as \
it is, and the voice says the rest; "showcase" only when using or wearing the product \
the way the line claims is itself the proof, such as a bag carried as a clutch or worn \
crossbody hands-free, or a still shot of it when the line is about exactly where it fits or \
how it looks. """
    + PROOF_RULE
    + """ In a "shows the problem" scene, the problem in plain \
view is what proves its line. When no kind fits, the scene is not B-roll.
- Who is in it: "has face" when the presenter's face is in the scene, "no face" when it \
isn't. A hand or a body without a face is "no face". The only person ever shown is the \
presenter: never anyone else.
- Its usage: how the product is used in the scene, read from the page's "how to use" or a \
scene the shop owner approved, and plan the scene with it. Null if the product isn't used \
in it.
- For "does a job", its result: what you can see at the end, which the scene ends on, \
such as "the spill wiped away". Only a result the page states or the shop owner approved. \
A result is filmed happening, from before to after in the same clip. A clean or finished \
thing with the product beside it, with no change filmed, never shows a result. """
    + RESULT_SHOWN
    + """ Null \
for the other kinds.
- Whether it is about a thin film: true when the problem it shows, or the result it ends \
on, is a thin film, haze, cloudiness or water spots on a surface. Then its needs list a \
photo that shows it.
- Its needs: only how a thing looks that the main photo can't show, such as what a gel \
looks like out of the tube. Never an action, a pose or a movement, such as a drop or a \
hand holding it: the action is said in words only. Give each need with the library numbers \
of the pictures that show it. The main \
photo is picked from the product photos, one showing the product in its colour when one \
does; leave needs empty when it's enough. A needed picture may be any of the library's \
pictures, even one where the product isn't clearly seen. Never name a photo that shows the \
product only in another colour than the ad's.
"""
)

# A rewrite keeps all evidence needs; the scene agent chooses the actual drawing inputs.
BROLL_DETAILS_INSTRUCTIONS = (
    BROLL_PLAN_DETAILS_INSTRUCTIONS.removesuffix("\n")
    + """ A scene's needs are suggestions for its scene agent when drawing the starting \
picture. The agent chooses its drawing photos; the video model receives only that \
starting picture and, when planned and usable, a cut piece of the page video for movement. \
Product photos and the presenter's portrait never go directly to the video model.
"""
)

# What each scene picture's `for` value means, told to the planner. The values are
# jobs.library.For, the one list (not approved yet: audit item 11).
SCENE_PICTURE_FOR: dict[For, str] = {
    "content": "what goes into or comes out of the product",
    "result": "the end state the scene ends on",
    "in-use": "the product used the way this scene uses it",
    "place": "where the scene happens",
    "person": "how the person, or their hand, is with the product",
    "state": "the product in a state the scene's action changes it to or shows",
}
assert tuple(SCENE_PICTURE_FOR) == get_args(get_args(For)[0]), "every For value is told, in order"

# The planner's suggested drawing photos and page-video movement reference.
PLAN_SCENE_PICTURES_INSTRUCTIONS = (
    """\
- Its scene pictures: the pictures the scene needs from the library, which holds every \
picture the app has of the product, each by its library number. Give them in the order \
they matter, each by its library number with what it is for: """
    + "; ".join(f'"{value}" for {meaning}' for value, meaning in SCENE_PICTURE_FOR.items())
    + """. Name as many as the scene needs, including a side photo when the scene needs \
that side. When the scene's end state depends on the \
product, such as what it makes, leaves behind or changes, name a picture of that end \
state, a page photo or a frame from a video, for "result". Empty for a scene where the \
person talks to camera.
- Its video piece: when a video moment shows the scene's action, that moment by its video \
and moment number, named the first time you plan the scene. Never give seconds. Null when \
no moment shows it.
Plan only actions \
the product really does: ones the page, the shop owner's words or a video moment shows it \
doing. Give each B-roll scene a different action: never film the same action in two \
scenes.
The scene agent chooses the photos for drawing its starting picture, using these scene \
pictures as suggestions. The video model sees only that starting picture and, when one \
is planned and usable, the cut piece of the page video for movement. Product photos \
never go directly to the video model.
"""
)

# What a B-roll scene films, for the planner and a rewrite alike. Graded #8 toilet (4
# steps in one clip) and #5 bag (strap on and off in one clip) failed; one step was perfect.
# No exception for steps that flow together: N5's clip jumped where Boreal skipped the middle.
# Round 2 fixes free check (10 Oct): #8's flush got a B-roll scene of its own, with stains
# still in the bowl and another toilet than the scrub's. Julian 02:59: never for a finishing step.
BROLL_ONE_ACTION_INSTRUCTIONS = """\
A B-roll scene films one action: one movement, the one moment that proves what its line \
claims. When the page or the line lists steps for using the product, film only the main \
step, the one that shows it working, such as the cloth wiping a stain away rather than \
spraying, waiting and rinsing, and write that B-roll line about that step. This holds \
even for steps that flow into each other with no pause: the video skips the middle of a \
chain of movements, so film only the main one. A finishing step that isn't the product \
doing its job, such as rinsing, flushing, drying or putting it away, never gets a B-roll \
scene of its own: the voice says it over the main step's scene, or the person says it to \
camera. Putting the product on or taking it off, attaching, fitting or adjusting it is a \
movement of its own: a scene films it or what comes after it, never both. A hand never \
turns, flips or spins the product, and it never spins by itself, to show it or another \
side of it: the side facing the camera when the scene starts faces it when the scene \
ends, even after a drop or fall. The only turn filmed is one the page says is part of \
using it. To show another side, such as the front and the back, give that side its own \
B-roll scene that starts on it. Never show a side or part no photo shows. Write what the \
scene shows with one verb for what moves, such as "a hand pours the sauce", never two \
joined by "and" or "then", such as "lifts and pours". A part the page says happens while \
doing it stays, joined by "while". The action is what a hand or the product does. A \
camera movement may accompany that action; it does not replace it. Still pick only ONE \
action. Write that one action the way the page words it. If the page says something \
happens while doing it, keep that part.
"""

# How long a B-roll scene's one action lasts, for the planner only. Round 5 cause 2: every
# one-moment action stretched over a 5-7 s clip broke, 37 clips and no exception. Julian
# 2026-10-19 00:44: "the action must fill the clip" ... "I approve this one."
BROLL_FILLS_CLIP_INSTRUCTIONS = f"""\
That one action lasts the whole clip, which is as long as its line takes to say and never \
under {LEAST_BROLL_SECONDS} seconds: by its own course, such as a steady pour or a walk, \
or by what it naturally sets off in the same scene, such as a cushion tossed onto a sofa \
that bounces and settles. An action that is over in a moment never gets a scene that ends \
as the moment ends, and never gets a second action added to fill the time. When no single \
action can last the whole clip, the person says that line to camera.
"""

# How long the end card plays after the last scene (spec #401: at least 1.5 s). Its time
# counts toward the ad's length: the default cap and the owner's target include it.
END_CARD_SECONDS = 2
# The voice words' pace rule, as the planner is told it and as a plan breaking it is answered.
NEVER_SLOW = (
    "The voice speaks at a natural, lively ad pace: never describe it as slow, unhurried, "
    "leisurely or taking its time."
)
# The planned line lengths, as the planner is told them.
TYPICAL_WORDS = typical_words_in_a_line(FIXED_WORDS_PER_SECOND)
# The hook just fills the shortest clip. That is also the fewest words any line may have,
# so the hook is exactly that long; its spoken style is shared with every talking line.
HOOK_WORDS = (
    f"Its first sentence lands the problem or the claim in about "
    f"{words_said_in(HOOK_POINT_SECONDS, FIXED_WORDS_PER_SECOND)} words, so the viewer hears "
    f"it within {HOOK_POINT_SECONDS} seconds, and the whole hook is exactly "
    f"{most_words_in_the_shortest_clip(FIXED_WORDS_PER_SECOND)} words."
)
SPOKEN_LINE_RULE = (
    "Every talking line sounds like a real person telling a friend about something she "
    "found, out loud. Keep the same facts, numbers and product name as the page or the "
    "shop owner states them; add nothing about the product that they don't say. Use "
    "everyday spoken words, never in the page's marketing copy; contractions are fine. "
    "Use two short sentences, or a sentence and a short fragment, with a turn between "
    "them: the setup or problem, then her reaction or the payoff. At least two of these "
    'appear: a short spoken opener ("Okay,", "So,", "Look,", "Honestly,", varied, not in '
    "every line); her own opinion or feeling, which needs no source; pointing at the "
    'product as "this". Say it from her side ("I", "my"), not as a product claim. No '
    "question to the viewer unless she'd really ask it. Spell numbers out as words; "
    "keep the product name exactly as stated, including any digits in its name. "
)
# What a line may say without the page or the shop owner stating it, as the planner, the fact
# check and its rewrite are told it: the viewer's problem and her opinion aren't product claims.
NOT_A_CLAIM = (
    "The viewer's problem in everyday words, such as a phone dying halfway through the day, "
    'and the person\'s own opinion or excitement, such as "you need this" or "I\'m '
    'obsessed", are not product claims and need no source. Only facts about the product '
    "need the page or the shop owner: what it is, does or contains, what it costs and the "
    "results it gives, including that it solves the problem. A performance promise or an "
    'absolute, such as "instantly", "guaranteed" or "the world\'s number one", is a fact '
    "about the product and needs them too."
)

PLAN_INSTRUCTIONS = (
    "You are the producer of a short vertical video ad for one product. A person "
    "speaks to camera, one line per scene, and each scene shows the product. You get, "
    "first, the moments of the shop's product videos, one a line: the video's number, "
    "the moment's number, its seconds, what happens and what is on screen, each moment "
    "one action. The video moments show how the product is really used: plan from "
    "them. Then how many pictures the library holds, the library being every picture "
    "the app has of the product, and how many of them you aren't shown. Then the "
    "library's pictures, several to a sheet, each with its library number and its "
    "label written under it. Name a picture by its library number, everywhere you name "
    "one. A picture marked as from the product's video is a product photo like every "
    "other, showing the product's looks at that moment; the line beside it says what "
    "it shows that no photo shows. On a sheet, that line is the label written under "
    "the picture. After the pictures come what the product's video says: the words "
    "said in each of the shop's product videos, one per video; the product page's "
    "visible text, followed by any product data the page declares for search engines; "
    "the product's link; the target length in seconds (or null if the shop owner "
    "didn't set one); and the conversation with the shop owner so far, each message "
    'labelled "user" for the shop owner or "producer" for you.\n'
    "Every claim in the ad must come from the page or the shop owner's own words. What "
    "the product's video says is the shop's own words, used like the page text; \"music "
    'only" means it says nothing. Your own messages are there only to show what was '
    "asked: never take a fact from them, except what a scene shows, how the product is "
    "used in it and its result when you proposed it and the shop owner then approved "
    "it: that, with any change they asked for, counts as their own words. The photos "
    "are only for the product's colour and how it looks: never take any other fact "
    "from them, such as text on a label. Never infer, guess or make anything up: not a "
    "price, a size, a material, a benefit or a colour.\n"
    'Decide "plan" when you can plan the whole ad from what you have. Give the scenes '
    "in the order they play, with each scene's line exactly as the person will say it. "
    "The ad is about one idea: the product's main benefit. It plays in three parts: "
    "the hook, the body and the call to action; give each scene the part it plays. The "
    "hook is the first scene alone: one line that makes the viewer stop and watch, "
    "starting with the problem or a surprising claim, not the product's name, and built on "
    "the page's strongest true point: the best reason to buy that the page, the product's "
    f"videos or the shop owner state. {HOOK_WORDS} {SPOKEN_LINE_RULE}{NOT_A_CLAIM} "
    "Every claim is said only once in the ad. The call "
    "to action is the last scene alone: it says the price unless the shop owner has "
    "asked to leave it out, and always tells the viewer to get it now, with a reason "
    "to act now, such as a discount, only if the page or the shop owner gives one; "
    'never "learn more". '
    f"{BRAND_RULE} When you give a brand, the last line says it in that short call to action. "
    f"After the last scene the ad ends on its end card, held {END_CARD_SECONDS} seconds and "
    "drawn for you: the product photo, "
    "its name, the website, and the brand, end card line and end card price you give. "
    "Copy the end card line word for word from the page text: one short line, the "
    "product's strongest true point. Copy the end card price exactly as the page writes "
    "it, the price a buyer pays today; leave it empty when the shop owner asked to leave "
    "the price out, when a price question is still unanswered, or when the page gives no "
    "price. For the body, choose the one format "
    "that suits the product and give it as the script's format; never mix two. The "
    "body is the scenes between the hook and the call to action, and the format's "
    "parts play in the order given across those scenes:\n"
    '- "problem, agitate, solve": the problem, agitate, solve: the problem named '
    "clearly, what it costs not to solve it, then the product as the answer. Best for "
    "products that fix a pain, health and wellness, and productivity.\n"
    '- "feature cascade": the hero feature, a supporting feature, proof: the most '
    "impressive thing the product does, one or two more, then a quick demo of a "
    "result. Best for tech and products with many features.\n"
    '- "before and after": the before state, the transformation moment, the after '
    "state: the problem shown, the product in action, then the better result. Best for "
    "beauty, fitness, home improvement and cleaning products.\n"
    '- "day in the life": the routine, the key moment, the result: the product in '
    "everyday life, its main use, then the payoff. Best for lifestyle products.\n"
    "A problem the product solves, a feature or a result is only one the page or the shop "
    "owner states: never make one up; only the everyday words that describe the problem "
    "are yours. With a target length, write only as many words as fit it when "
    "spoken at an easy pace. Say the product's name, as the page states it (often the "
    "brand or a short name), in at least one line. Give the product's name exactly as "
    "the person says it in one of those lines, copied word for word from it: not the "
    "page's full title, and nothing the person doesn't say, such as a part in brackets "
    "or a symbol like ® or ™. When the shop owner has asked to leave the price out, "
    "no line says a price and no overlay shows one, even if the page gives a price. "
    "Otherwise, one line says the product's price: the price a buyer pays today, so "
    "on a sale, the sale price. Give a scene an overlay, a few words "
    "drawn along the top of the picture while it plays, when there is something worth "
    "showing as well as saying, such as the price or the product's name; otherwise set "
    "it to null. An overlay states only what the page or the shop owner states, like a "
    "line. Name the product's colour as the photos show it, one of the product's "
    "colours as the answer lists them, and give the library numbers of the pictures "
    "that show the product in that colour. A photo counts whenever this product is "
    "clearly seen in it in that colour, even beside other items, such as in a pack, a "
    "set, a line-up or beside its other colours: the picture is told which item to "
    "take. Leave out a photo where this product can't be clearly seen, and give none "
    "when no photo shows it clearly in that colour. If the product comes in several "
    "colours, don't ask which: pick the colour with the most photos where the product "
    "is clearly seen; on a tie, the colour of picture 1, the library's first. Say how "
    'big the product is, judged from the photos you chose: "tiny" if it fits on a '
    'fingertip, such as earrings, earbuds or a ring; "handheld" if it\'s held in one or '
    'two hands, such as a bottle, a bag, a power bank or a rolled-up mat; "large" if '
    "it can't be held, such as a chair, a treadmill or a mattress. The size decides "
    "how the person is posed with the product in every scene where they talk to "
    "camera, so judge it by what a person could really hold. Describe the person who "
    "presents the ad: whether they are a man or a woman, how they look, for a "
    "portrait, and how their voice sounds, for speech made inside every clip. The "
    "portrait and each clip's voice are made from these words, and both are of the "
    "same person, so the looks and the voice must agree with the gender you set and "
    f"with each other. {NEVER_SLOW} Describe only the person and where they are: never the "
    "product, "
    "anything they hold, any animal, or what they do in a scene. Choose someone who "
    "suits the product and its buyers, and never a real, famous person. Set question "
    "to null.\n"
    "The first scene is always the person talking to camera. "
    f"{MIDDLE_IS_BROLL} {AT_LEAST_ONE_BROLL} For each scene ask: can "
    "she show what this line says with the product in her hands or on herself, at "
    'arm\'s length? If yes, choose kind "talking": her face is in frame and she talks '
    "while showing the product, using it, or showing its result. Showing the product "
    "to the lens counts as its action; never a plain talking head. "
    f"{LENS_FIRST_OR_LAST} If no, choose kind "
    '"broll": hands and product only, her face out of frame, and her voice speaks off '
    "camera. Give kind_why: what her hands cannot show. For either kind, shows names "
    "its one action with the product, never null. The action and every claim must come "
    "from the page, the product photos or the shop owner's words. The first scene "
    "stays talking even when it can only show the product to the lens. "
    f"{STYLE_AND_STATE} The styles: {styles_said()}. {DIFFERENT_STYLE_AND_STATE} "
    f"{BOX_NEEDS_PACKAGING} A B-roll scene has no talking style or product state. "
    "With no target "
    "length, plan 3 or 4 scenes: the hook, 1 or 2 body scenes and the call to action, "
    "aiming for about 30 seconds, never over 35 seconds in whole clips and the end card. "
    f"Write lines of about {TYPICAL_WORDS[0]} to {TYPICAL_WORDS[1]} words at "
    f"{FIXED_WORDS_PER_SECOND} words a second. With a target, use as "
    "many scenes as it needs and fit the owner's length, which counts the end card too. "
    f"Every line must take at least {SHORTEST_BROLL_LINE_SECONDS} and at most "
    f"{LONGEST_BROLL_LINE_SECONDS} seconds "
    f"({fewest_words_in_a_broll_line(FIXED_WORDS_PER_SECOND)} to "
    f"{most_words_in_a_broll_line(FIXED_WORDS_PER_SECOND)} words) at that "
    "speed. H3 makes the voice from the same voice description in every clip; no voice "
    "file is made in advance. Each action fills the clip and ends with an action after "
    "the last word. The one-action rule below applies to both kinds. A B-roll scene's "
    "action is in no other scene; talking scenes may share one, such as the first and the "
    "last scene both showing the product to the lens.\n"
    "For every scene, give its proof: "
    + PROOF_IS
    + ". The scene's action must really show it. "
    + PROOF_RULE
    + "\n"
    "A B-roll scene films the product being used only in a way it is shown how: a photo "
    "shows the product used that way, or the page's text or the shop owner's own words "
    "say how it is held and moved: which hand holds it, what moves and what it touches. "
    "Naming a use, listing its steps or describing it in general words doesn't show how. "
    "An ordinary use anyone can picture from the words needs neither, such as pouring "
    "from a bottle or zipping up a jacket: film its plain visible part. For a use nobody "
    "could picture from the words, ask the shop owner, as below.\n"
    "A B-roll scene films only what a still camera could watch happening from outside, the "
    "whole time. Something that happens inside a closed or running thing, or out of sight, "
    "is never a scene: say that claim while filming another visible action that proves a "
    "claim from the page, or choose a talking scene where the presenter shows or uses the "
    "product while speaking.\n"
    "A B-roll scene films one action: one movement, the one moment that proves what "
    "its line claims. When the page or the line lists steps for using the product, "
    "film only the main step, the one that shows it working, such as the cloth wiping "
    "a stain away rather than spraying, waiting and rinsing, and write that B-roll "
    "line about that step. This holds even for steps that flow into each other with no "
    "pause: the video skips the middle of a chain of movements, so film only the main "
    "one. A finishing step that isn't the product doing its job, such as rinsing, "
    "flushing, drying or putting it away, never gets a B-roll scene of its own: the "
    "voice says it over the main step's scene, or the person says it to camera. "
    "Putting the product on or taking it off, attaching, fitting or adjusting it is a "
    "movement of its own: a scene films it or what comes after it, never both. A hand "
    "never turns, flips or spins the product, and it never spins by itself, to show it "
    "or another side of it: the side facing the camera when the scene starts faces it "
    "when the scene ends, even after a drop or fall. The only turn filmed is one the "
    "page says is part of using it. To show another side, such as the front and the "
    "back, give that side its own B-roll scene that starts on it. Never show a side or "
    'part no photo shows. Write what the scene shows with one verb for what moves, such as "a '
    'hand pours the sauce", never two joined by "and" or "then", such as "lifts and '
    'pours". A part the page says happens while doing it stays, joined by "while". The '
    "action is what a hand or the product does. A camera movement may accompany that "
    "action; it does not replace it. Still pick only ONE action. Write that one action "
    "the way the page words it. If the page says something happens while doing it, "
    "keep that part.\n"
    "A scene's result is what the camera sees when its one action ends. When a claim "
    "needs two end states, give each its own B-roll scene, back to back, each with its "
    "own line and its one action, and mark the second as the second state. A claim "
    "needs two end states in two cases. The product can be used in two different ways "
    "that can't be seen at once, such as a jacket worn on either side: film each way "
    "with the product already set up that way, never the change from one way to the "
    "other. Or the proof is seen only after the action ends, such as a dropped item's "
    "unharmed other side: the drop scene ends with it lying the same side up, and the "
    "next B-roll scene starts on the other side, never turning it over. Never more "
    "than two scenes for one claim. Never show before and after pictures of bodies or "
    "skin, a screen whose content you'd have to invent, a result the page doesn't "
    "state, or parts of the product no photo shows, other than a scene's owner part.\n"
    # Ticket #427: the same words as the picture form; no check reads them.
     + LABEL_RULE + " " + PLAN_OPEN_RULE + "\n"
    "What B-roll can show depends on the kind of product. As a guide (what it can "
    "show; only if the page says; never):\n"
    "- Beauty and skincare: a texture close-up (a dab on a fingertip), hands applying "
    "it, the pack; only if the page gives the texture and how it's applied; never skin "
    "before and after, skin problems or a visible result on skin.\n"
    "- Food, drink and kitchen: a pour, sizzle and steam, a cooking step, the finished "
    "dish; only foods or recipes the page names and what the cookware does; never "
    "other foods, half-eaten food or chewing.\n"
    "- Fashion: fabric close-up, the garment moving, a detail, worn and turning; only "
    "the material, fit and features the page gives; never fit or stretch it doesn't "
    "claim.\n"
    "- Home and cleaning: the product in a room, hands using it, setup, a surface "
    "before and after; only the result the page states; never a stronger result.\n"
    "- Tech and gadgets: design close-up, the feature working in hands, an everyday "
    "setting, unboxing; only features and box contents the page lists; never invented "
    "screens or readable small print.\n"
    "- Fitness equipment: in use, folded away; only the exercise and size claims the "
    "page makes; never body changes.\n"
    "- Supplements and health: the routine, such as a scoop into a shaker; only how "
    "it's taken; never any body, weight or health outcome.\n"
    "- Cars: driving on a road, design details, the interior; only a car the page "
    "names; never performance it doesn't claim or other brands' cars.\n"
    "- Car accessories: fitted in a car, in use, an installation step; only cars and "
    "installing the page describes.\n"
    "- Apps, software and services: only screens the shop owner supplies.\n"
    "- Pets: the pet using the product; only the animal and use the page names; never "
    "health outcomes.\n"
    "- Kids, baby and toys: a toy mid-play with hands only, the gear's features on the "
    "product alone; only claims the page makes; never children.\n"
    "- Jewelry: an extreme close-up, worn on a hand or neck; only the material and "
    "stones the page gives; never detail the photos don't show.\n"
    "You may go against the guide for an unusual product if your reason says why.\n"
    "For each B-roll scene, also give:\n"
    '- Its kind: "does a job" when the product does something you can see, such as a '
    'pan searing a steak or a cloth wiping a spill away; "shows the problem" when the '
    "line names the problem the product fixes, as the page names it: the problem as it "
    "really is before the product is used, with the product in view. Only a problem "
    "anyone sees at a glance, or one a photo shows. Never the problem getting worse or "
    'building up over time: show it as it is, and the voice says the rest; "showcase" '
    "only when using or wearing the product the way the line claims is itself the "
    "proof, such as a bag carried as a clutch or worn crossbody hands-free, or a still "
    "shot of it when the line is about exactly where it fits or how it looks. "
    + PROOF_RULE
    + ' In a "shows the problem" scene, the problem in plain view '
    "is what proves its line. When no kind fits, the scene is not B-roll.\n"
    '- Who is in it: always "no face", hands and product only. A hand or a body '
    'without a face is "no face". The only person ever shown is the presenter: never '
    "anyone else.\n"
    "- Its usage: how the product is used in the scene, read from the page's \"how to "
    'use" or a scene the shop owner approved, and plan the scene with it. Null if the '
    "product isn't used in it.\n"
    '- For "does a job", its result: what you can see at the end, which the scene ends '
    'on, such as "the spill wiped away". Only a result the page states or the shop '
    "owner approved. A result is filmed happening, from before to after in the same "
    "clip. A clean or finished thing with the product beside it, with no change "
    "filmed, never shows a result. " + RESULT_SHOWN + " Null for the other kinds.\n"
    "- Whether it is about a thin film: true when the problem it shows, or the result "
    "it ends on, is a thin film, haze, cloudiness or water spots on a surface. Then "
    "its needs list a photo that shows it.\n"
    "- Its needs: only how a thing looks that the main photo can't show, such as what "
    "a gel looks like out of the tube. Never an action, a pose or a movement, such as "
    "a drop or a hand holding it: the action is said in words only. Give each need "
    "with the library numbers of the pictures that show it. The main photo is picked "
    "from the product photos, one showing the product in its colour when one does; "
    "leave needs empty when it's enough. A needed picture may be any of the library's "
    "pictures, even one where the product isn't clearly seen. Never name a photo that "
    "shows the product only in another colour than the ad's.\n"
    "- Its scene pictures: the pictures the scene needs from the library, which holds "
    "every picture the app has of the product, each by its library number. Give them "
    "in the order they matter, each by its library number with what it is for: "
    '"content" for what goes into or comes out of the product; "result" for the end '
    'state the scene ends on; "in-use" for the product used the way this scene uses '
    'it; "place" for where the scene happens; "person" for how the person, or their '
    'hand, is with the product; "state" for the product in a state the scene\'s action '
    "changes it to or shows. Name as many as the scene needs, including a side photo "
    "when the scene needs that side. When the scene's end state depends on the "
    "product, such as what it makes, leaves behind or changes, name a picture of that "
    'end state, a page photo or a frame from a video, for "result". Either kind may '
    "name drawing photos.\n"
    "- Its video piece: when a video moment shows the scene's action, that moment by "
    "its video and moment number, named the first time you plan the scene. Never give "
    "seconds. Null when no moment shows it.\n"
    "Plan only actions the product really does: ones the page, the shop owner's words "
    "or a video moment shows it doing. Give each B-roll scene a different action: "
    "never film the same action in two scenes.\n"
    "The scene agent chooses the photos for drawing its starting picture, using these "
    "scene pictures as suggestions. The video model sees only that starting picture "
    "and, when one is planned and usable, the cut piece of the page video for "
    "movement. Product photos never go directly to the video model.\n"
    'Decide "ask" about the price only when the shop owner hasn\'t asked to leave it out '
    "and you don't know the one price to say, because neither the page nor the shop "
    "owner gives a price, or they give different prices to choose between "
    '(such as a single item, a pack and a subscription). Also decide "ask" when the '
    "page says two different things about a fact a line or an overlay would state, "
    "so that writing it would mean guessing which is true. A fact no line or overlay "
    'states is never a reason to ask. Also decide "ask" when photo_worry says the photos '
    "are in doubt, or no photo clearly shows the product: ask them to attach more or "
    "go ahead with the photos we have. A photo where the product is small beside "
    "pictures of other products, such as a chart of the devices it works with, or half "
    "hidden by text, doesn't clearly show it.\n"
    "Ask because of a scene that shows the product in only four cases, and nothing else:\n"
    "- The scene needs something of the product that no photo shows and the video "
    "would have to guess, such as what a serum looks like out of the bottle or a gel "
    "coming out of its tube. Ordinary things around the product are never missing: a "
    "phone, a hand, a bowl of noodles can be shown without a photo. A photo that shows "
    "it, even one where the product isn't clearly seen, such as blush on a cheek, is "
    "enough.\n"
    "- The product does a job you can see, such as a cleaner or a pan, and neither the "
    "page nor the shop owner says how it is used. Ask even if you could plan it only "
    "showing the product: an ad for such a product shows it doing its job. A product "
    'that doesn\'t do a job you can see, such as a bag, needs no "how to use": never '
    "ask for one.\n"
    "- The scene films the product being used in a way it isn't shown how, and nobody "
    "could picture it from the words: no photo shows it used that way and neither the "
    "page nor the shop owner says how it is held and moved.\n"
    "- The scene ends on a result, what the product removes or prevents is a thin "
    "film, haze, cloudiness or water spots on a surface such as glass, tile, a mirror "
    "or chrome, and no photo shows that surface before and after. Ask even when "
    "everyone knows the word: a camera barely sees a thin film, so the video would "
    "guess how it looks and how much changes. The page naming the result isn't enough: "
    "the video would still guess how it looks. When what it removes is a coloured mark "
    "anyone sees at a glance, such as a stain, a ring, rust, mud, grease or dirt, "
    "never ask: show that mark, even if the page also names a film.\n"
    "Then say in plain words what's missing. For something of the product no photo "
    "shows, offer three answers: attach a photo of it; describe how it looks in one "
    "sentence; or have you draw it from your own idea of it. Say that they will see "
    "the scene's picture and say yes to it before its video is made. For the other "
    'cases, offer two answers: attach a photo of it (for a missing "how to use", of '
    "the product being used; for a result, of it before and after: without one, the ad "
    "shows no before and after of it; for a line nothing proves, of what would prove "
    "it; for a use it isn't shown how, of the product being used that way, or one "
    "sentence on how it is used), or go ahead without one. Never choose for them. For "
    "something of the product no photo shows: if they attach a photo, plan with it "
    "among the scene's needs; if they describe it, give the scene an owner part from "
    "the shop owner's words, its looks their sentence as they said it; if they say to "
    "draw it, give the scene an owner part you invent, its looks one sentence of how "
    'such a thing ordinarily looks: the only thing you may make up. For a missing "how '
    'to use": include your proposed demo in this same first question, next to the request '
    "for a photo of it in use: say in plain words how you'd show it and ask them to approve "
    "or change it. Their approval is their own words, and for a product that does a job, "
    "it gives the scene's usage and result. Without their explicit approval, do not film "
    "that use: the person says its line to camera. For a result: if they go ahead without one, "
    "no scene shows that film, "
    "before or after, and the voice says the claim over another B-roll that proves its "
    "own line, or, when none does, the person says it to camera. When a line cannot be "
    "demonstrated, a talking scene can show the product to the lens while she says it, "
    f"but {LENS_ONLY}. For a use it "
    "isn't shown how: if they go ahead without one, the person says that line to "
    "camera, or the voice says it over another B-roll scene that proves its own line. "
    "Never offer another way to show it. Before you ask, go through every rule above, "
    "every price, photo and scene, and find everything you need to ask about: ask "
    "about all of it in this one message, so the shop owner answers it all at once. "
    "Write each item in question_items with its kind and a short, specific question. "
    "Code numbers them and appends that kind's approved no-answer default. Include the "
    "proposed demo in a demo item next to the in-use-photo question, in this same first "
    "message. Set plan to null. After any owner answer, plan with their answers and use "
    "the stated defaults for anything unanswered; never ask another pre-plan question. "
    "For a plan after the question round, list each item the owner actually answered in "
    "question_resolutions: its number in the shown message and a verbatim answer_quote from "
    "the owner's later messages. Include only items their words actually answer or override; "
    "acknowledging the message or updating something else does not resolve an item. Never "
    "quote the producer or infer an answer. Unlisted items retain their stated defaults. "
    "Use an empty list when nothing was answered or there was no question round. "
    "The planning_defaults handoff tells you which defaults apply. Use page_conflict for a "
    "narrow question about two different things the page says about a fact a line or "
    "an overlay would state, other than the price; it keeps waiting for an answer.\n"
    "For a plan, also write how the product behaves: one short paragraph, at most "
    "three sentences, from the page's text and the video moments, saying in plain "
    "words what this product does when it is used: what moves, what comes out of it "
    "and what it leaves. Only what the page or the moments show. It is read by whoever "
    "checks the clips.\n"
    "Give one sentence saying why: for a plan, why this many scenes; for a question, "
    "why you need to ask. Write it for the shop owner. You may say what the ad would "
    "show, but never tell them which scenes show the product rather than the person "
    "talking, or that there are two kinds of scene."
)


# An overlay longer than this would wrap into a second line, down towards the face.
MOST_OVERLAY_CHARACTERS = 30


# What a B-roll scene is: the product doing something you can see, the problem it fixes, or
# the product used or worn the way its line claims, such as a bag carried as a clutch.
BrollKind = Literal["does a job", "shows the problem", "showcase"]

# Whether a B-roll scene shows the presenter's face. Only a face matters: a hand or a body
# never does, and the only face ever shown is the presenter's, from their portrait.
PersonShown = Literal["no face", "has face"]


class Need(BaseModel):
    what: str = Field(description='What the scene needs, in a few words, such as "the gel".')
    photos: list[StrictInt] = Field(
        min_length=1, description="The library numbers of the pictures that show it."
    )

    @field_validator("what")
    @classmethod
    def _not_empty(cls, what: str) -> str:
        if not what.strip():
            raise ValueError("This can't be empty.")
        return what


def packaging_photo_number(number: int | None) -> int | None:
    """`number` as a packaging photo: a library number, which starts at 1, or None."""
    if number is not None and number < 1:
        raise ValueError("packaging_photo is a picture's library number, from 1, or null for none.")
    return number


class ScriptScene(BaseModel):
    """A scene's line and, for a B-roll scene, what it shows and its B-roll details, as the
    planner gives them and a rewrite gives them back."""

    kind: Literal["talking", "broll"] | None = Field(
        default=None,
        description="Whether the person talks on camera or speaks off camera over hands "
        "and product.",
    )
    kind_why: str = Field(
        default="",
        description="For B-roll, what cannot be shown with the product in her hands or on "
        "herself at arm's length.",
    )
    line: str = Field(description="Exactly what the person says in this scene.")
    shows: str | None = Field(
        default=None,
        description="For a B-roll scene, what it shows while the person's voice says the "
        "line, in plain words. Null for a scene where the person talks to camera.",
    )
    broll_kind: BrollKind | None = Field(
        default=None,
        description='For a B-roll scene, "does a job" when the product does something you can '
        'see, "shows the problem" for the problem it fixes before it is used, or "showcase" '
        "when using or wearing it the way the line claims is the proof, such as a bag carried "
        "as a clutch, or a still shot of it when the line is about exactly where it fits or how "
        "it looks. Null for a scene where the person talks to camera.",
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
        "on. Null for the other kinds and for a scene where the person talks to camera.",
    )
    thin_film: bool = Field(
        default=False,
        description="True when the problem this scene shows, or the result it ends on, is a "
        "thin film, haze, cloudiness or water spots on a surface.",
    )
    needs: list[Need] = Field(
        default_factory=list,
        description="For a B-roll scene, only how a thing looks that the main photo can't "
        "show, never an action or a pose, each with the numbers of the photos that show it. "
        "Empty when the main photo is enough, and for a scene where the person talks to "
        "camera.",
    )
    # Spec #401's fields, kept with the scene; the whole-plan rules over them are in
    # jobs.plan_rules.
    talking_style: TalkingStyle | None = Field(
        default=None,
        description="For a talking scene, how the presenter is filmed; never the same as the "
        "previous talking scene's. Null for B-roll.",
    )
    product_state: ProductState | None = Field(
        default=None,
        description='For a talking scene, where the product is: "out of the box" is the '
        "product alone, no box shown; never the same as the previous talking scene's. Null "
        "for B-roll.",
    )
    proof: str = Field(
        default="",
        description="What the viewer sees in this scene that proves its line: the scene's "
        "action really shows it.",
    )
    shows_to_lens: bool = Field(
        default=False,
        description="True when the scene's action only shows the product to the lens.",
    )
    packaging_photo: StrictInt | None = Field(
        default=None,
        description='For "in its box" or "beside its box", the library number of the photo '
        "that shows the packaging. Null otherwise.",
    )

    # A validator rather than min_length, which OpenAI's structured output doesn't accept.
    @field_validator("line")
    @classmethod
    def _says_something(cls, line: str) -> str:
        if not line.strip():
            raise ValueError("A scene's line can't be empty.")
        return line

    @field_validator("packaging_photo")
    @classmethod
    def _a_library_number(cls, number: int | None) -> int | None:
        return packaging_photo_number(number)

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
        if self.kind == "talking":
            if self.shows is None:
                raise ValueError(
                    "A talking scene needs an action with the product, not a plain talking head."
                )
            return self
        if self.shows is None:
            if self.has_broll_details():
                raise ValueError(
                    "A scene where the person talks to camera has no B-roll kind, person, "
                    "usage, result or needs."
                )
            return self
        if self.broll_kind is None:
            raise ValueError(
                'A B-roll scene needs its kind: "does a job", "shows the problem" or "showcase".'
            )
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
            raise ValueError(f'A "{self.broll_kind}" scene has no result.')
        # A camera barely sees a thin film: without a photo of it the video guesses, and
        # before looked the same as after (test ads, 08 Oct, N3).
        if self.thin_film and not self.needs:
            raise ValueError(
                "A scene about a thin film needs a photo that shows it among its needs. "
                "Without one, make no scene about it: the voice says it over another B-roll."
            )
        return self

    def has_broll_details(self) -> bool:
        """Whether any of the B-roll details is given."""
        return any(
            (
                self.broll_kind,
                self.person_shown,
                self.usage,
                self.result,
                self.needs,
                self.thin_film,
            )
        )

    def is_broll(self) -> bool:
        """Whether the scene is B-roll: said so, or, with no kind given, it shows something."""
        return self.kind == "broll" or self.kind is None and self.shows is not None

    def quality_fields(self) -> dict[str, Any]:
        """Spec #401's fields as a Scene stores them: a B-roll scene has no talking style,
        product state or packaging photo."""
        broll = self.is_broll()
        return {
            "talking_style": "" if broll else self.talking_style or "",
            "product_state": "" if broll else self.product_state or "",
            "proof": " ".join(self.proof.split()),
            "shows_to_lens": self.shows_to_lens,
            "packaging_photo": None if broll else self.packaging_photo,
        }

    def plan_scene(self, number: int) -> PlanScene:
        """The scene as the whole-plan rules read it, as scene `number`."""
        return PlanScene(
            number=number,
            broll=self.is_broll(),
            style=self.talking_style,
            state=self.product_state,
            shows_to_lens=self.shows_to_lens,
            packaging_photo=self.packaging_photo,
        )

    def broll_details(self) -> dict[str, Any]:
        """The B-roll details as a Scene stores them: blank for a talking scene."""
        return {
            "broll_kind": self.broll_kind or "",
            "person_shown": self.person_shown or "",
            "usage": self.usage or "",
            "result": self.result or "",
            "needs": [need.model_dump() for need in self.needs],
        }


def same_action(shows: str) -> str:
    """What a B-roll scene films, as code compares two scenes: its words, whatever their
    case, punctuation or articles."""
    words = re.findall(r"[\w']+", shows.casefold())
    return " ".join(word for word in words if word not in {"a", "an", "the"})


# Why a plan with a B-roll scene whose action another scene has is refused, after "Scenes 1
# and 3": talking scenes may share one, as the graded Creatify ads that opened and closed with
# her showing the product to the lens did (docs/agents/proven-broll-rules.md).
SAME_ACTION = (
    "have the same action: a B-roll scene's action is in no other scene, though talking scenes "
    "may share one, such as the first and the last scene both showing the product to the lens."
)


def repeated_action(scenes: Iterable[tuple[int, bool, str]]) -> tuple[int, int] | None:
    """The first two of `scenes`, each its number, whether it is B-roll and its action, that
    have the same action when one of them is B-roll, or None."""
    seen: list[tuple[int, bool, str]] = []
    for number, broll, shows in scenes:
        action = same_action(shows)
        for first, first_broll, first_action in seen:
            if first_action == action and (broll or first_broll):
                return first, number
        seen.append((number, broll, action))
    return None


def needs_missing(scene: ScriptScene, library: Collection[int]) -> str | None:
    """Why a scene's needs name a picture the library doesn't hold, or None."""
    return pictures_missing([number for need in scene.needs for number in need.photos], library)


def pictures_missing(numbers: list[int], library: Collection[int]) -> str | None:
    """Why `numbers` name a picture the library `library` doesn't hold, or None."""
    for number in numbers:
        if number not in library:
            return f"There's no picture {number} in the library."
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


# Where an owner part's looks come from: the shop owner's own sentence, or the planner's
# when they asked it to draw the part.
OwnerPartSource = Literal["owner's words", "invented"]


class OwnerPart(BaseModel):
    """Something of the product no photo shows, drawn from the shop owner's words or invented
    by the planner, that the shop owner approves in the starting picture before the scene's
    clip is made."""

    what: str = Field(description="What it is, in a few words.")
    source: OwnerPartSource = Field(
        description='"owner\'s words" when the shop owner described how it looks, "invented" '
        "when they asked you to draw it."
    )
    looks: str = Field(
        description="How it looks, in one sentence: the shop owner's, their sentence as they "
        "said it, or yours when they asked you to draw it."
    )

    @field_validator("what", "looks")
    @classmethod
    def _not_empty(cls, text: str) -> str:
        if not text.strip():
            raise ValueError("This can't be empty.")
        return text


def _quoted(values: tuple[str, ...]) -> str:
    """`values` quoted and listed: "a", "b" or "c"."""
    quoted = [f'"{value}"' for value in values]
    return f"{', '.join(quoted[:-1])} or {quoted[-1]}"


class ScenePicture(BaseModel):
    """A picture a B-roll scene needs, by its library number, with what it is for."""

    model_config = ConfigDict(validate_by_name=True, serialize_by_alias=True)

    number: StrictInt = Field(description="The picture's library number.")
    for_: For = Field(
        alias="for",
        description=f"What it is for: {_quoted(get_args(get_args(For)[0]))}.",
    )


class ScenePiece(BaseModel):
    """The video moment a B-roll scene's action is cut from, by number: never by seconds."""

    video: StrictInt = Field(description="The video's number.")
    moment: StrictInt = Field(description="The moment's number in that video.")


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
    owner_part: OwnerPart | None = Field(
        default=None,
        description="For a B-roll scene that shows something of the product no photo shows, "
        "after the shop owner described it or asked you to draw it: what it is, where its "
        "looks come from and how it looks. Null otherwise, and when a photo shows it.",
    )

    scene_pictures: list[ScenePicture] = Field(
        default_factory=list,
        description="For a B-roll scene, its scene pictures in the order they matter, each by "
        "library number with what it is for. Empty for a scene where the person talks to "
        "camera.",
    )
    piece: ScenePiece | None = Field(
        default=None,
        description="For a B-roll scene whose action a video moment shows, that moment by its "
        "video and moment number. Null otherwise.",
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

    @model_validator(mode="after")
    def _owner_part_is_filmed(self) -> Self:
        if self.owner_part is not None and self.shows is None:
            raise ValueError("A scene where the person talks to camera has no owner part.")
        return self

    @model_validator(mode="after")
    def _only_broll_is_sent_pictures(self) -> Self:
        if self.shows is None and (self.scene_pictures or self.piece is not None):
            raise ValueError(
                "A scene where the person talks to camera has no scene pictures or video piece."
            )
        return self


# How big the product is. Code owns the pose for each size (jobs.scenes.POSES), so a
# chair is never asked for at chest height and studs are never lost on an open palm.
ProductSize = Literal["tiny", "handheld", "large"]

# What the person presents as. Code puts it into both the portrait's prompt and H3's
# voice words (work.voice_words, unless the voice already says it); a plan that
# leaves it out is refused, so neither model is left to pick a gender at random and
# disagree with the other.
PersonGender = Literal["man", "woman"]


# The plan's ad colour (#273 point 2).
PRODUCT_COLOUR_ANSWERED = (
    "The product's colour as the photos show it: one of the product's colours, as the answer "
    "lists them."
)


class Plan(BaseModel):
    scenes: list[PlannedScene] = Field(min_length=1)
    product_name: str = Field(
        description="The product's name copied word for word from a line, exactly as the "
        "person says it there: not the page's full title."
    )
    product_colour: str = Field(description=PRODUCT_COLOUR_ANSWERED)
    # Advice for the picture's photo, never a gate: it may be empty (Julian 2026-10-22).
    colour_photos: list[StrictInt] = Field(
        description="The library numbers of the pictures showing the product in that colour; "
        "empty when none shows it clearly."
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
    product_behaviour: str = Field(
        description="How the product behaves when it is used, one short paragraph from the "
        "page text and the video moments.",
    )
    script_format: ScriptFormat | None = Field(
        default=None,
        description="The one format the ad's body plays in, between its hook and its call to "
        "action.",
    )
    # Spec #401's plan fields, kept with the job. A plan for a job is held to them by
    # end_card_problem.
    brand: str = Field(
        default="",
        description=f"The product's brand. {BRAND_RULE} When it is given, the last scene's "
        "line says it.",
    )
    end_card_line: str = Field(
        default="",
        description="The end card's line: one short line copied word for word from the page "
        "text, its strongest true point.",
    )
    end_card_price: str = Field(
        default="",
        description="The price on the end card, written exactly as the page writes it: the "
        "price a buyer pays today. Empty when the shop owner asked to leave the price out, "
        "when a price question is still unanswered, or when the page gives no price.",
    )

    @field_validator("product_name", "product_colour", "person_looks", "person_voice")
    @classmethod
    def _not_empty(cls, text: str) -> str:
        if not text.strip():
            raise ValueError("This can't be empty.")
        return text

    @field_validator("person_voice")
    @classmethod
    def _never_slow(cls, voice: str) -> str:
        if slow := asks_for_a_slow_pace(voice):
            raise ValueError(f'The voice asks for a slow pace ("{slow}"). {NEVER_SLOW}')
        return voice

    @field_validator("product_behaviour")
    @classmethod
    def _one_paragraph(cls, behaviour: str) -> str:
        if not behaviour.strip():
            raise ValueError("This can't be empty.")
        if "\n" in behaviour.strip():
            raise ValueError("How the product behaves is one short paragraph.")
        return behaviour.strip()

    @model_validator(mode="after")
    def _a_different_action_in_each_broll_scene(self) -> Self:
        repeated = repeated_action(
            (number, scene.kind == "broll" or scene.kind is None, scene.shows)
            for number, scene in enumerate(self.scenes, start=1)
            if scene.shows is not None
        )
        if repeated is not None:
            first, again = repeated
            raise ValueError(f"Scenes {first} and {again} {SAME_ACTION}")
        return self

    @model_validator(mode="after")
    def _broll_and_talking_scenes_that_differ(self) -> Self:
        broken = rule_broken(
            [scene.plan_scene(number) for number, scene in enumerate(self.scenes, start=1)]
        )
        if broken is not None:
            raise ValueError(broken)
        return self

    @model_validator(mode="after")
    def _opens_on_the_person(self) -> Self:
        first = self.scenes[0]
        if first.kind == "broll" or first.kind is None and first.shows is not None:
            raise ValueError("The first scene is the person talking to camera: its shows is null.")
        return self

    @model_validator(mode="after")
    def _owner_part_has_no_photo(self) -> Self:
        # A part a photo shows is drawn from the photo: only a part no photo shows waits for
        # the shop owner's yes.
        for number, scene in enumerate(self.scenes, start=1):
            if scene.owner_part is None:
                continue
            if any(same_thing(need.what, scene.owner_part.what) for need in scene.needs):
                raise ValueError(
                    f'Scene {number} has a photo of "{scene.owner_part.what}": a part a photo '
                    "shows is a need, never an owner part."
                )
        return self

    @model_validator(mode="after")
    def _second_state_after_the_first(self) -> Self:
        # The two end states of one claim play back to back, so the second's clip can be made
        # from the first's (audit row 11).
        for number, scene in enumerate(self.scenes, start=1):
            if not scene.second_state:
                continue
            if scene.kind == "talking" or scene.shows is None or number == 1:
                raise ValueError(
                    f"Scene {number} is said to camera: only a B-roll scene shows a second state."
                )
            first = self.scenes[number - 2]
            if first.kind == "talking" or first.shows is None:
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
    def _name_said(self) -> Self:
        name = " ".join(self.product_name.split()).casefold()
        if not any(name in " ".join(scene.line.split()).casefold() for scene in self.scenes):
            raise ValueError(f'No line says "{self.product_name}": at least one must.')
        return self


class H3PlannedScene(PlannedScene):
    kind: Literal["talking", "broll"] = Field(
        description="Talking: face in frame, speaking while showing, using or showing a "
        "result. B-roll: hands and product only, voice off camera."
    )
    shows: str | None = Field(description="The one action the scene films, for either kind.")

    @model_validator(mode="after")
    def _kind_matches_the_shot(self) -> Self:
        if not self.shows:
            raise ValueError("Every scene needs its action with the product.")
        if self.kind == "broll":
            if not self.kind_why.strip():
                raise ValueError("A B-roll scene needs why it cannot be shown at arm's length.")
            if self.person_shown != "no face":
                raise ValueError(
                    "A B-roll scene has hands and product only: its face is out of frame."
                )
        return self


class QuestionItem(BaseModel):
    """One numbered item in the planner's single question, with a default owned by code."""

    model_config = ConfigDict(extra="forbid")

    kind: QuestionKind
    question: str = Field(
        min_length=1,
        description="One specific question, including a proposed demo when asking for approval.",
    )

    def with_default(self) -> str:
        fallback = default_for(self.kind)
        return (
            f"{self.question.strip()} If you don't answer: {fallback}"
            if fallback
            else f"{self.question.strip()} I'll wait for your answer."
        )


class QuestionResolution(BaseModel):
    """An answered question item, supported by the owner's words after it was shown."""

    model_config = ConfigDict(extra="forbid")

    item: StrictInt = Field(description="The answered item's number in the question message.")
    answer_quote: str = Field(
        min_length=1,
        description="Verbatim owner words answering or overriding this item, from a message "
        "after the questions were shown. No acknowledgement, unrelated note, or inferred answer.",
    )


class ProducerDecision(Judgement):
    decision: Literal["plan", "ask"]
    question: str | None = Field(
        description='The complete question message for the shop owner, if "ask".'
    )
    question_items: list[QuestionItem] = Field(
        default_factory=list, description="Every pre-plan question, all at once. Empty for a plan."
    )
    question_resolutions: list[QuestionResolution] = Field(
        default_factory=list,
        description="Items the owner actually answered, with their words as evidence. "
        "Empty when asking, when no item was answered, or when there was no question round.",
    )
    plan: Plan | None = Field(description='The plan, if "plan".')

    @model_validator(mode="after")
    def _plan_or_question(self) -> Self:
        if (
            self.decision == "ask"
            and "question_items" in self.model_fields_set
            and not self.question_items
        ):
            raise ValueError("An ask decision needs every numbered question item and its default.")
        if self.decision == "ask" and self.question_resolutions:
            raise ValueError("An ask decision cannot resolve questions not yet shown.")
        if self.decision == "plan" and self.question_items:
            raise ValueError("A plan decision cannot contain question items.")
        if self.decision == "ask" and self.question_items:
            self.question = "\n".join(
                f"{number}. {item.with_default()}"
                for number, item in enumerate(self.question_items, 1)
            )
        if self.decision == "plan" and (self.plan is None or self.question is not None):
            raise ValueError('A "plan" decision needs a plan and no question.')
        if self.decision == "ask" and (not self.question or self.plan is not None):
            raise ValueError('An "ask" decision needs a question and no plan.')
        return self


def producer_decision_for(
    *,
    library: Collection[int],
    moments: Mapping[int, int],
    colour: Any = str,
    target_seconds: int | None = None,
    photo_worry: str = "",
    questions_resolved: bool = False,
    plan_check: Callable[[ProducerDecision], None] | None = None,
    page_text: str | None = None,
) -> type[ProducerDecision]:
    """The producer's decision for a job with the library numbers in `library` and, for each
    watched page video by its number, how many moments it has, its ad colour held to
    `colour` (jobs.colours.answer: one of the product's colours). A plan naming a library
    picture or a moment the job doesn't have fails while the answer is read, like any other
    broken plan. With the job's `page_text`, so does one whose brand or end card isn't the
    page's (end_card_problem)."""
    scene_type: Any = list[H3PlannedScene]
    plan_for_job = create_model(
        Plan.__name__,
        __base__=Plan,
        scenes=(scene_type, Field(min_length=1)),
        product_colour=(colour, Field(description=PRODUCT_COLOUR_ANSWERED)),
    )

    class ProducerDecisionForJob(ProducerDecision):
        plan: plan_for_job | None = Field(description='The plan, if "plan".')

        @model_validator(mode="after")
        def _questions_before_planning(self) -> Self:
            if self.plan is not None and photo_worry and not questions_resolved:
                raise ValueError(
                    "The photos are in doubt: ask about them, with every other question you have. "
                    + photo_worry
                )
            if (
                self.decision == "ask"
                and photo_worry
                and not any(item.kind == "photos" for item in self.question_items)
            ):
                raise ValueError(
                    "The photos are in doubt: include the photo question with every other "
                    "question you have."
                )
            if self.decision == "ask" and questions_resolved:
                raise ValueError(
                    "The owner has already answered the one question message. Plan with that "
                    "answer and the stated defaults for unanswered items; do not ask again."
                )
            if self.plan is not None and plan_check is not None:
                plan_check(self)
            return self

        @model_validator(mode="after")
        def _scene_count(self) -> Self:
            if (
                target_seconds is None
                and self.plan is not None
                and not 3 <= len(self.plan.scenes) <= 4
            ):
                raise ValueError(
                    "With no target length, plan 3 or 4 scenes: the hook, 1 or 2 body "
                    "scenes and the call to action."
                )
            return self

        @model_validator(mode="after")
        def _every_scene_proved(self) -> Self:
            if self.plan is None:
                return self
            for number, scene in enumerate(self.plan.scenes, start=1):
                if missing := proof_missing(number, scene):
                    raise ValueError(missing)
            return self

        @model_validator(mode="after")
        def _brand_and_end_card(self) -> Self:
            if self.plan is not None and page_text is not None:
                problem = end_card_problem(self.plan, page_text)
                if problem is not None:
                    raise ValueError(problem)
            return self

        @model_validator(mode="after")
        def _real_photos(self) -> Self:
            if self.plan is None:
                return self
            for missing in (
                pictures_missing(self.plan.colour_photos, library),
                *(needs_missing(scene, library) for scene in self.plan.scenes),
                *(
                    picture_missing(number, scene, library)
                    for number, scene in enumerate(self.plan.scenes, start=1)
                ),
                *(
                    moment_missing(number, scene, moments)
                    for number, scene in enumerate(self.plan.scenes, start=1)
                ),
                *(
                    packaging_missing(number, scene, library)
                    for number, scene in enumerate(self.plan.scenes, start=1)
                ),
            ):
                if missing is not None:
                    raise ValueError(missing)
            return self

    return ProducerDecisionForJob


def proof_missing(number: int, scene: ScriptScene) -> str | None:
    """Why scene `number` is refused for having no written proof, or None."""
    if scene.proof.strip():
        return None
    return f"Scene {number} has no proof: give every scene its proof, {PROOF_IS}."


def end_card_problem(plan: Plan, page_text: str) -> str | None:
    """Why `plan`'s brand or end card isn't the page's, or None (spec #401): a brand given
    must be in the page text and said in the last scene's line (an empty one, for a page
    whose text never writes its brand, is never checked: #423), the end card line must be the
    page's own words, and a price on the end card must be on the page. Words are matched as
    jobs.word_check.tidy writes them, so "$24" and "24 dollars" are the same; a price by its
    amount (_price_on_page)."""
    from .word_check import tidy

    page = tidy(page_text)
    brand = plan.brand.strip()
    if brand and not _has_words(page, tidy(brand)):
        return f'The brand "{brand}" isn\'t in the page text. {BRAND_RULE}'
    if brand and not says_the_brand(plan.scenes[-1].line, brand):
        return f'The last scene\'s line must say the brand, "{brand}", in a short call to action.'
    line = plan.end_card_line.strip()
    if not tidy(line):
        return "Give the end card one short line copied word for word from the page text."
    if not _has_words(page, tidy(line)):
        return (
            f"The end card line \"{line}\" isn't the page's own words: copy one short line "
            "word for word from the page text."
        )
    price = plan.end_card_price.strip()
    if tidy(price) and not _price_on_page(price, page_text):
        return (
            f'The end card price "{price}" isn\'t on the page: write the price exactly as '
            "the page gives it, or leave it empty."
        )
    return None


# An amount as written, such as "24", "1,299" or "24.99".
_AMOUNT = r"\d+(?:,\d{3})*(?:\.\d+)?"
_CURRENCY_SIGNS = "$€£¥₹"
# An amount of money on a page: an amount with a currency sign before it or a currency sign
# or code after it, such as "$24.00", "24.00 USD" or "24 €". A bare number, such as "30
# customers", is never a price.
_PRICED = re.compile(
    rf"(?<![\d.,])(?:[{_CURRENCY_SIGNS}]\s*({_AMOUNT})|({_AMOUNT})\s*(?:[{_CURRENCY_SIGNS}]|[A-Z]{{3}}\b))"
)
_ANY_AMOUNT = re.compile(rf"(?<![\d.,]){_AMOUNT}(?![\d,]*\d)")


def _price_on_page(price: str, page_text: str) -> bool:
    """Whether `price` is on the page: its one amount is an amount of money the page gives,
    however the page writes its currency ("$24.00" and "24.00 USD" alike), or, for a price
    with no amount, its words are the page's."""
    from .word_check import tidy

    amounts = _ANY_AMOUNT.findall(price)
    if not amounts:
        return _has_words(tidy(page_text), tidy(price))
    if len(amounts) > 1:
        return False
    priced = {_amount(before or after) for before, after in _PRICED.findall(page_text)}
    return _amount(amounts[0]) in priced


def _amount(written: str) -> float:
    return float(written.replace(",", ""))


def says_the_brand(line: str, brand: str) -> bool:
    """Whether `line` says `brand`, matched as jobs.word_check.tidy writes them."""
    from .word_check import tidy

    return _has_words(tidy(line), tidy(brand))


def _has_words(text: str, words: str) -> bool:
    """Whether tidied `text` holds tidied `words` whole, never as part of a longer word."""
    return re.search(rf"(?<!\w){re.escape(words)}(?!\w)", text) is not None


def picture_missing(number: int, scene: PlannedScene, library: Collection[int]) -> str | None:
    """Why scene `number` names a scene picture the library doesn't hold, or None."""
    for picture in scene.scene_pictures:
        if picture.number not in library:
            return (
                f"Scene {number} names picture {picture.number}: the library has no such picture."
            )
    return None


def packaging_missing(number: int, scene: ScriptScene, library: Collection[int]) -> str | None:
    """Why scene `number`'s packaging photo isn't a picture the library holds, or None."""
    if scene.is_broll() or scene.packaging_photo is None or scene.packaging_photo in library:
        return None
    return (
        f"Scene {number} names picture {scene.packaging_photo} as its packaging photo: the "
        "library has no such picture."
    )


def moment_missing(number: int, scene: PlannedScene, moments: Mapping[int, int]) -> str | None:
    """Why scene `number` names a moment no watched video has, or None. `moments` holds how
    many moments each watched video has, by the video's number."""
    return piece_missing(number, scene.piece, moments)


def piece_missing(number: int, piece: ScenePiece | None, moments: Mapping[int, int]) -> str | None:
    """Why scene `number`'s `piece` names a moment no watched video has, or None."""
    if piece is None:
        return None
    video, moment = piece.video, piece.moment
    if not moments.get(video):
        return f"Scene {number} names a moment of video {video}: that video has no moments."
    if not 1 <= moment <= moments[video]:
        return (
            f"Scene {number} names moment {moment} of video {video}: that video has moments "
            f"1 to {moments[video]}."
        )
    return None


class ChatMessage(Handoff):
    """One message in the conversation with the shop owner: theirs ("user"), or the
    producer's."""

    by: Literal["user", "producer"]
    text: str


class PlanHandoff(Handoff):
    # In the order the planner reads them (#210 point 9): the moments first, then the
    # library's sheets (the call's pictures), then the rest.
    AFTER_IMAGES = frozenset(
        {"video_transcripts", "page_text", "product_url", "target_seconds", "conversation"}
    )

    moments: str
    library_pictures: int
    pictures_not_shown: int
    video_transcripts: list[str]
    page_text: str
    product_url: str
    target_seconds: int | None
    conversation: list[ChatMessage]
    photo_worry: str = ""
    planning_defaults: str = ""


def check_opening_kind(number: int, kind: str) -> None:
    """The hook stays on camera when a scene is edited or moved."""
    if number == 1 and kind == "broll":
        raise ValueError("The first scene must stay talking to camera.")
