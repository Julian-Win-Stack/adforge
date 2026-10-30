# AdForge

A chat app that turns a product page link into a finished short UGC video ad. The user talks to the producer in one box; it reads the page, plans the ad, creates a presenter, makes each scene and assembles the video, asking questions when something is missing rather than guessing.

## Language

### The conversation

**Session**:
One chat thread, named and returned to. Holds the whole conversation and every ad made in it.
_Avoid_: Chat, thread, conversation, project

**Message**:
One turn in a session, from the user or the producer, or a notice. The only way the user and AdForge communicate.
_Avoid_: Comment, note, activity entry

**Notice**:
What the code records, not the producer, when a step fell back to a worse source or failed, or what it tells the user they can act on. Every notice has a kind, and its kind decides who sees it (spec #272): the user's chat shows only what they must act on (send a page or photo, answer, say yes, pick a try, watch a clip that couldn't be checked) or a reply to something they just did, in plain words, red only when the ad stopped and needs them. Every other kind (fallbacks, retries, safety-filter refusals, setup problems) goes only to the **developer record**. Every notice is kept with the job as one of its warnings, and none is given to a model. A scene's starting picture or clip also gets a notice when it starts being made ("Making scene 2's clip."); when it ends with nothing made, the chat gets a notice with no words, which only tells the scene's card, and why is recorded.
_Avoid_: error message (for a fallback the user can't act on)

**Developer record**:
Where every notice is kept for the developer: the job's warnings (read through the warnings API and by the run scripts' warnings.py) and Sentry, where each kind of failure is one issue, tagged with the session, ad, scene and step and linked to its Langfuse trace.

**Nudge**:
A note code gives the producer, never shown to the user, when it went to stop with the ad unfinished and nothing running or waiting on the user: carry on, or say why you can't. At most 2 per user message.

**Attachment**:
A file a message carries: a photo the user attached, or a picture, sound or video the producer made. Kept through the file store and shown in the chat. A scene's starting picture or clip carries its scene's number.
_Avoid_: Media, upload, asset

**Brief**:
What the user asks for in their own words: the link, the length, a promo code, anything else. Not a form and not a stored record of its own.
_Avoid_: Request, prompt, order

**Interrupt**:
A message that arrives while the producer is working. The producer decides what it means for work already in flight.
_Avoid_: Cancel, abort

**Pause**:
The shop owner's button that stops new work until they write again. Scene steps not yet taken by the worker are cancelled (free), running ones finish and are kept, a running fix finishes its current try and parks, and the producer is told which in one system note. While paused, every producer tool that would start a step answers "paused", so the producer reads the shop owner's next message before starting anything; that message ends the pause.
_Avoid_: Stop, hold, cancel

### The agents

**Agent**:
A model in a loop that chooses which tool to call next. Two are built: the producer, and the fixing agent, which films a scene's clip again (see Auto-fix); the critic comes later.
_Avoid_: Bot, assistant

**Producer**:
The one agent the user talks to, for the whole session (ADR 0005, ADR 0010). Reads the page, asks, plans, creates the person and the music, makes every scene and assembles the ad; after the first clip it acts on every message of the shop owner's with the same tools (a plan edit, a new place, new references, a scene step, a swap to a kept version, a fix); a version the shop owner puts in the ad from a scene's card is swapped in by code, and the producer is only told, then tells them in one line what it changed and what it cost and shows the new ad. It never waits for a yes, except the owner-part gate. The only agent that speaks in the chat.
_Avoid_: Orchestrator, main agent, "the agent"

**Look**:
A call the producer makes on its own model to see a picture, a clip's frames, a kept version, a library item or a sent list. It answers in words only, in the judge's fault shape (what is wrong, the root cause, a direction), and decides nothing, so the producer's own conversation never holds the pictures. Its own call purpose in the gateway.
_Avoid_: Decider, critic, look_at

### The work

**Job**:
One ad being made. A session holds as many as the user asks for.
_Avoid_: Ad, run, task, order

**Variant**:
A job that points at another job in the same session as the one it varies, so the two can be compared. Variants are siblings, not versions of each other.
_Avoid_: Version, copy, alternative

**Scene**:
One spoken line in a job, and everything produced for it. A scene has no planned length: it lasts exactly as long as its line takes to say.
_Avoid_: Shot, beat, clip, segment

**Talking scene**:
A scene in which the person says the line to camera. Every scene is one unless the plan gives it something to show, and the first scene always is.
_Avoid_: A-roll, presenter scene

**B-roll scene**:
A scene that shows the product rather than the person talking, such as a pan sizzling, while the person's voice says the line over it. What it shows is written in plain words in the plan, with its kind, who is in it, its usage, its result and the photos it needs (its B-roll details). What it shows, its usage and its result are fact checked like the line, and a scene that fails is rewritten whole, B-roll details included. Its clip is made by Boreal-H3 (on Creatify) from its sent list, never from a starting picture. Made with the same tools as a talking scene. The user is never told which scenes are which, except when a B-roll scene has to become a talking scene: a notice says so, with the reason.
_Avoid_: Cutaway, product shot, insert

**Scene step**:
One piece of a scene's work, run in the background: its starting picture, its line's audio, the audio's transcript, and its clip. A scene tool starts it and returns at once; when it finishes or fails, the producer is told on its next turn and tells the user.
_Avoid_: Task, job, render

**Starting picture**:
A talking scene's first frame, made from the portrait and one product photo: the person holding the product. The talking clip is made from it as its first frame. Talking scenes only: a B-roll scene has none, and its clip is made from its sent list (spec #210 point 19).
_Avoid_: Start frame, keyframe, thumbnail

**Before-picture**:
One picture per ad of the place its B-roll scenes happen in, before any of them starts: each thing the scenes act on, with every problem any of them shows, and no product or person. Drawn once, when the ad's room alone is first needed, and the room alone is drawn from it, so the scenes match.
_Avoid_: Base picture, background, set

**Line's audio**:
The person's voice saying a scene's line, exactly as the line stands. Never shown to the user. A new line needs new audio.
_Avoid_: Voiceover, recording, clip

**Transcript**:
What was heard in a line's audio, word by word with when each was said, exactly as spoken: nothing tidied or matched to the line. Kept with the audio it was heard in.
_Avoid_: Captions, subtitles

**Caption**:
A few of a line's words drawn along the bottom of the finished ad while they are said: the words as the line writes them, each timed from when the transcript heard it. Timed from where the scene's line is said in the ad, which can be over the end of the scene before's picture.
_Avoid_: Subtitle, lower third

**Overlay**:
The on-screen text for one scene, such as the price, written by the producer when it plans the ad; a scene may have none. Drawn in a fixed band along the top while the scene plays, clear of the face and the product. Not fact checked yet.
_Avoid_: Caption, graphic, text layer, on-screen text

**Script format**:
The structure an ad's body plays in, between its hook and its call to action, chosen by the producer when it plans the ad to suit the product: problem, agitate, solve; feature cascade; before and after; or day in the life (Creatify's body structures). An ad uses one, never two.
_Avoid_: Template, framework, ad format

**Part**:
What a scene does in the script: the hook first, the call to action last, and the script format's parts between them, in their order, such as "before state". The hook is a line that makes the viewer stop and watch; the call to action says the price and tells the viewer to get it now. Each is one scene.
_Avoid_: Beat, section, stage

**Second state**:
A B-roll scene showing the second of two end states one claim needs: the second way the product can be used, such as a bag carried as a clutch after it is worn crossbody, or the proof seen only after an action ends, such as a dropped item turned over unharmed. It plays just after the B-roll scene showing the first, and one claim has two such scenes at most, so its clip can be made to match the first's.
_Avoid_: Pair, variant, alternate use

**Clip**:
A scene's moving picture, with its line's audio: the same audio its transcript was heard in. A talking scene's is its starting picture animated to speak that audio, so it lasts exactly as long. A B-roll scene's is made by Boreal-H3 with no sound, from its sent list, in the fewest whole seconds that cover the audio (at least 5, at most 15). It isn't cut to its line: the audio is laid over its start, and it is kept whole, silent after the line, so its motion plays out. A B-roll line whose audio is too long for any clip is shortened before a clip is paid for, or, after 3 shortenings, said to camera instead. Made only once the audio is heard (and, for a talking scene, its starting picture made), for the line as it stands. A scene is finished once its clip's step is, after the clip judge and any fixing agent, never as its first clip is kept; a talking scene's new starting picture plans it again until a clip is made from that picture. Shown to the user in the chat as soon as it is made, with its scene's number.
_Avoid_: Video, render, shot

**Finished ad**:
Every scene's clip put together in order, with the captions, each scene's overlay and the music under the voice. The voice never stops: each line starts where the last one ends. A B-roll scene's clip plays whole, so the next line starts over its end (the early cut). A talking scene after it skips the start of its picture by as much, so its lips match its words; a second B-roll scene in a row plays from its own start, its picture a little behind its line; a B-roll scene that ends the ad plays its end over the music. The ad lasts as long as its picture. Shown to the user in the chat. Made again as a new version when a clip, an overlay or the music changes; the old one is kept.
_Avoid_: Final video, render, output

**Person**:
The presenter in an ad: one portrait and one matching voice. Every scene in a job shows the same person. The plan says whether they are a man or a woman, and code puts that into both the portrait's prompt and the voice's description, so the two can't disagree.
_Avoid_: Avatar, actor, character, persona

**Product size**:
How big the product is, judged by the plan from the photos: tiny (fits on a fingertip: earrings, earbuds), handheld (held in one or two hands: a bottle, a rolled mat) or large (can't be held: a chair, a treadmill). Decides the pose.
_Avoid_: Scale, dimensions, category

**Pose**:
How the person is placed with the product in a talking scene: one fixed sentence per product size, owned by code. The same sentence is handed to the model that plans the starting picture and put into the clip's motion prompt, so the picture and the clip never disagree about where the product is. Every pose keeps the top and bottom of the frame clear for the overlay and the captions.
_Avoid_: Framing, composition, blocking

**Page text**:
The words on the product page about the product this page sells, plus the structured product data the page declares for search engines. A model copies this product's passages out of the page word for word, and code keeps only the copied sentences it finds on the page, so text about other products sold alongside it ("pairs well with", bundles) and anything the model made up never gets in. The only place facts about the product may come from. Models read this, never the HTML. The whole visible text is kept beside it, for looking things up, but no model writes from it.
_Avoid_: Page content, scraped text, HTML

**Product photo**:
A photo of the product this page sells, kept with the job; scenes are made from them. A model picks them off a screenshot of the page with every picture numbered, comparing them with the shop's own record of the product and its official photos, so photos of other products on the page ("you may also like", bundles, line-ups) are left out. **Hidden pictures** are shown to it beside the screenshot, and so is every official photo. A picture under 100 px on a side is never one. Each is fetched at its biggest size, copies of one photo are kept once, gallery first, up to 50. When the page can't be screenshotted, the picker fails or the page blocked our reader (a block, error, login or "verify you're human" page), the photos the page declares for search engines are used instead, and a notice says so. When photos were declared ones or fewer than 2 were kept, the user is shown them and the ad isn't planned until they answer: go ahead, or attach their own.
_Avoid_: Image, picture (for these), asset

**Hidden picture**:
A picture on the product page that the screenshot can't show because a scroll box or a carousel slide clips it, as the rest of a gallery that scrolls. The page script numbers it with the box that hides it, and the photo picker is shown it in a labelled grid beside the screenshot.
_Avoid_: Off-screen image, lazy image

**Product video**:
A video the shop placed with the product photos in the product's gallery, picked off the page with them; reviews, creator videos and banners are not product videos. Kept with the job as its file with its first frame, frames spread over it and its length; when no file can be had (an embedded player, a stream, a dead link) as its link, with the page's poster or the player's thumbnail as its first frame. A page's videos are fetched at the same time. A page read again only adds the videos the job doesn't have yet, so each is watched once. Every file up to 3 minutes long is watched by the **Watcher**, all at once, before the plan: its **Moments**, a **Look frame** per product state (into the **Library**), the product's colour, and the **Transcript of a product video** (the words said in it, as the watcher heard them; "music only" when nothing is said). One the watcher says doesn't show this product is thrown out, and the owner is told in grey; one over 3 minutes isn't watched, and the owner is told which. The planner and the fact check get every transcript as the shop's own words, like the page text.
_Avoid_: Media, asset, clip (a clip is a made ad clip)

**Video picture**:
A product photo cut from a product video at one second: a look frame the watcher named, kept as a photo for the starting picture and the fact check; it carries the look frame's label. The planner sees the look frame itself on the library sheets, written under as "from the product's video N at S s". It counts as a product photo everywhere (looks only), after the page's photos and before the user's. A moment that copies a page photo is not kept. Never a reference photo, and never shown to the user.
_Avoid_: Frame, still, keyframe, video frame

**Page panel**:
The panel in the chat where the shop owner pastes the product page's text and attaches its photos, shown when the page can't be read: it can't be fetched, or its text is a loading screen, cookie wall, bot check or error page. It stays open until the app has what it asked for, or a page is read from a new link. The normal chat stays open under it.
_Avoid_: Shed, form, manual mode

**Owner-pasted page**:
Page text the shop owner pasted in the page panel. Code runs the same junk check on it as on a page, then keeps it as the page text, so the planner, the fact check and the line checks read it like a page that was read. The owner is the source of it.
_Avoid_: Override, manual input, pasted facts

**User-supplied fact**:
Something the user states that the page does not, such as a promo code or a replacement line. Used as written and not fact checked, because the user is the source.
_Avoid_: Override, manual input

### The library (spec #210)

Shapes in `jobs/library.py` (#211). Built by `jobs/library_items.py` (#220): every kept product photo after the page read (and each one added later), and each owner photo once cleaned. The planner and a scene's rewrite see it on **Library sheets** (`jobs/library_sheets.py`, #233); the round export writes it as `library.txt`.

**Library**:
Everything the app knows about one product's pictures and videos, built once per ad: every kept page photo, every owner photo cleaned, every page video watched (its moments, look frames and colour), the product-only version of each look photo that needs one, and recoloured versions of pictures. Each picture carries the picker's fixed fields (kind, side, alone, colour, target for a line-up) and one free label sentence code never parses.
_Avoid_: Asset pool, gallery, media

**Labelling call**:
The call that gives a photo the picker never saw (one the shop owner sent, or one the page only declares) the picker's fixed fields and label, by the picker's own label rules (`label_photo`).
_Avoid_: Tagger, classifier

**Library number**:
An item's number in the library, which never changes for the ad. Numbers run from 1 in the order items enter the library. The planner names every picture by it (scene pictures, needs, colour photos); a need or colour photo is kept as the product photo that picture is.
_Avoid_: Index, photo number (a product photo's position is another number)

**Library sheet**:
One picture the planner is shown with several library pictures on it (2 by 2, the grid one setting), each picture's number and label in a black bar under it, so its 50 pictures hold 200. The planner is handed the video moments first, as text, then the sheets. A library past 200 has its last pictures left off, and the chat is told how many in red.
_Avoid_: Grid (the picker's), contact sheet

**Look picture**:
A picture whose job is to show the product's look (a side or a state).
_Avoid_: Side picture (not yet built, another idea), reference picture

**Scene picture**:
A picture one scene needs (content, result, in use).
_Avoid_: Reference photo (the owner's photo on a note)

**Piece**:
A 2-15 s cut of a page video sent as a reference video.
_Avoid_: Clip (a clip is a made ad clip), segment

**Moment**:
One action in a page video, with its start and end seconds. Numbered per video from 1 in time order; a piece is named by its moment's number, never by seconds.
_Avoid_: Scene, shot, video picture

**Watcher**:
The model that watches a page video whole, with its sound (Gemini Flash), once per video as the page is read. It says whether the video shows this product and writes its moments (each one action of at most 15 s), its look frames, the product's colour and its transcript. It replaced the video reader and its thinned frames (ADR 0009). A video its safety filter refuses is asked again, then watched without its sound, then halved down to the refused seconds, which are dropped; the owner is told of every refusal in red.
_Avoid_: Video reader (the old one)

**Look frame**:
A frame of a page video the watcher named as the best view of one product state, pulled from the file at that second and kept in the library with the photo's fields, its video and its second.
_Avoid_: Video picture (its product-photo copy)

**Sent list**:
The numbered pictures and pieces one clip was sent, with their labels, kept on the clip with every picture left out and its tier, every recolour and its outcome, and the prompt.
_Avoid_: References, inputs, pictures sent (the starting picture's old record)

### Fixing a scene

**Note**:
The shop owner's own words about a scene of the ad they were shown, such as "the tea should actually pour". A note about what a clip shows is handed by the producer word for word to the fixing agent (fix_scene), with its own instruction and the look's words; anything else the producer does itself. Not a scene step's note, which is the instruction to the model making the step.
_Avoid_: Feedback, comment, complaint

**Fix**:
The fixing agent's work on one scene for a shop owner's note about what its clip shows, started by the producer's fix_scene as a background step, through the owner-note door: the note word for word, the producer's instruction and the look's words are its first message. It films silent tries (at most 6, each judged) and ends with the try that passed or, when the tries run out or it gives up, the last try kept in the ad with every try there to swap, as on a judge fail (one rule for both doors); either way the producer is told in a system note. A question the fixing agent has for the shop owner parks the fix and comes back to the producer, which asks in the chat and hands the answer back through fix_scene.
_Avoid_: Change request, edit, revision

**Fix plan**:
Gone (Oct 29, spec #247): the decider's per-scene plan of a round of notes, shown with a cost and a yes button and run by code. The producer acts on notes itself now.
_Avoid_: Fix list, proposal

**System note**:
What code tells the producer, in a short sentence, in its conversation: a scene step finished or failed (what it made and cost), a fix done or parked with its question, a pause with the steps it cancelled and kept, a version the shop owner put in the ad from a scene's card (told once code has assembled the ad with it), a stale mark, an error, the new ad shown. Never a picture or frames. The shop owner isn't shown it.
_Avoid_: Producer message, event

**Decider**:
Gone (Oct 29): the Oct 16 to Oct 29 agent that planned owner-note fixes; replaced by the producer and the look (ADR 0010).
_Avoid_: as a name for the look

**Fix check**:
The clip judge's one extra question on each try of a fix (door 2): shown the clip the shop owner noted beside the try, is the change they asked for made? Asked once. A "not fixed" fails the try as a judge fail does, and the fixing agent films another, until its tries run out; an answer that can't be had leaves the try passing, as the judge's own. Nothing is drawn again for it and nothing waits for the shop owner. Kept on the try.
_Avoid_: Fix judge, verification, QA

### Machinery

**Tool**:
One thing an agent can do, such as reading the page or making a scene's clip. The agent chooses which to call and in what order; the tool itself enforces the rules that matter.
_Avoid_: Step, action, function, capability

**Tool answer**:
What a tool hands the producer: the thing it made, "can't, because ..." with the reason, or one clarifying question. Every refusal outside the hard list is one; none stops the run.
_Avoid_: Error, refusal, result

**Hard list**:
The only three refusals of a wish that live in code (agents/hard_list.py), each a tool answer: a new product mid-ad, a clip of an invented part without the shop owner's yes, and the video API's limits. A new guard is added only after a graded failure.
_Avoid_: Guards, rules, blocks

**Stale**:
A scene step whose inputs changed since it ran (a new line, place, person or reference list). Code marks it and says so in the tool answer; the producer decides whether to run it again. Code never reruns a step on its own.
_Avoid_: Dirty, outdated, invalid

**Fold**:
Replacing the oldest tool calls in the producer's conversation by one line each (tool, scene, outcome, cost) once its last turn passes half the model's window, with a snapshot of the ad always present. Nothing is deleted from the record, and the shop owner sees nothing.
_Avoid_: Summary, truncation, compaction

**Exclude list**:
The library items never sent for one scene, set by the producer on the shop owner's word. It holds whatever tier an item falls in, and against the fixing agent, until a new note from the shop owner.
_Avoid_: Blocklist, banned pictures

**Why**:
The one sentence the producer writes with every tool call, stored with the call and shown on the per-ad page of what the agent did.
_Avoid_: Reason, rationale

**Checkpoint**:
The record of a finished tool call: which tool, its arguments, what it produced, what it cost. What a restart resumes from, so resuming never depends on the model remembering.
_Avoid_: State, snapshot, progress marker

**Gateway**:
The single place every model call passes through. Validates the handoff, retries a service that is down, and records the call with its cost. A call already paid for with the same handoff, whose answer a stopped worker never kept, is answered from its record instead of paid for again.
_Avoid_: Client, wrapper, adapter, provider

**Handoff**:
The validated data passed into a model call, and the validated shape expected back. Checked before money is spent.
_Avoid_: Payload, request body, contract

**Model call**:
One attempt at one model, recorded with its cost, duration, outcome and one-sentence reason, whether it succeeded or failed.
_Avoid_: API call, request, generation

**Measured speaking speed**:
How fast a specific voice actually talks, found by having it read the script and timing the result. Every length decision uses it. No fixed words-per-second value exists anywhere.
_Avoid_: Pacing, WPS, rate

**Replay**:
Round 4's ads made again with every starting picture and clip taken from what round 4 saved, and their voice and music faked (evals/owner_notes), so shop owners' notes can be sent to them for the price of the text models.
_Avoid_: Fixture ad, test ad

### Checks

**Planning check**:
A check that looks only at text, before anything is rendered. The fact check and the length fit. Always runs, including when quality checks are switched off.
_Avoid_: Validation, pre-flight, gate

**Fact check**:
Comparing every price, number, product name and claim in a line against the page text. A claim the page does not state counts as not matching. A line that has not passed cannot be rendered.
_Avoid_: Verification, accuracy check

**Quality check**:
A check on something already produced, such as a picture or a clip. On unless switched off as a whole. Built so far: the B-roll clip check, which judges the clip right after it is made against its sent list: first whether the references show enough to judge it, then whether the product is the real one and behaves like it (10 frames a second) and for movement or change no real camera could film (5 frames a second), and keeps its ruling on the clip. The starting-picture judge, which looks at a picture beside the shop photo it was drawn from for objects that can't exist, checks a fix's new talking picture.
_Avoid_: QA, review, gate

**Auto-fix**:
What happens when the B-roll clip check fails a scene's clip: the fixing agent takes the scene over at once. It sees the plan, the page, the shop photos, the place, the presenter and every try's frames with what the check said, decides what to change (the action, the result, the sent list, the motion prompt, the line), and films a new try; each try is checked too. It stops when the check passes a try, which is then the scene's clip, or when it judges the scene can't be filmed well, at most 6 tries in all. Every try is a version. When it gives up, the scene keeps its last try, whether the judge or the producer's fix_scene started it; the shop owner is shown every try, each with what the check saw, the agent's pick marked and why it stopped, and can put one in the ad from the scene's card, for nothing. The ad doesn't wait. The shop owner isn't asked anything first: no yes is needed for it. A first clip the check can't settle (no usable answer) is kept as it is, and the shop owner is told it went unchecked.
_Avoid_: Retry, redo (a redo is made for a fix), round (a round is one batch of owner notes)

**Clip version picker**:
A scene card's strip of every clip the scene was shown, tries included, numbered as its versions, each badged with what the clip check said (passed, failed, not checked), the one in the ad outlined (Julian, Oct 29). A tap previews a clip; "Use this in the ad" swaps it in by code for nothing, with Undo, and the ad is assembled again by itself. A clip that no longer says the scene's line is faded, tagged "Old line", and can't be swapped in. The one place the shop owner picks a clip.
_Avoid_: Previous/Latest, "Use this one"

**Try**:
One new clip of a scene the fixing agent films after the clip check failed it, from its own sent list. Numbered from 1; the clip the check failed first is the first clip. Every try is a version.
_Avoid_: Round, remake, attempt

**Version**:
One starting picture and the clip made from it (for a B-roll scene, which has no starting picture, one clip), as the shop owner was shown them, numbered from 1 in the order shown ("v2 black toilet"). A new picture starts one; a new clip of the same picture, each try, and anything brought back by going back is one of its own. The shop owner and the producer can swap to any kept version, for free.
_Avoid_: Variant (a variant is another job), copy

**Reference photo**:
A photo the shop owner attaches to a note, showing how something in a scene should look, such as the gel. It enters the library cleaned and labelled like any library item (a video attached with a note is watched into moments), and the producer sets it as a scene's reference with set_references, or hands it to the fixing agent with the note. It wins over the product photos wherever they disagree for the scenes it is set on, until the shop owner says otherwise.
_Avoid_: Reference picture, owner picture

**Reference mode**:
How B-roll clips were made from 2026-10-16 until the sent list replaced it (#241): the video model got the drawn starting picture as Image 1, told the clip starts like it, then the clean pictures of the reference photos that only show during the clip. A B-roll clip now has no starting picture: it is made from its sent list. A reference photo still goes in as a picture of the thing alone, drawn from it; no raw photo is ever sent.
_Avoid_: Reference picture mode, way 3

**Owner part**:
A thing of the product no photo shows, drawn from the shop owner's words or invented by the planner. It gets a picture of its own in the library (an OwnerPart item, one per part): drawn from its sentence beside a product photo, or the shop owner's photo of it cleaned. The shop owner must approve that picture before the clip of any scene holding the part is made (spec #210 point 20). The planner asks for a photo of it, one sentence describing it, or leave to draw it and show it first. The scene keeps what the part is, where its looks come from and one sentence of how it looks, and when the shop owner said yes and in which of their messages: the yes comes in their words in the chat, and the producer keeps it with approve_owner_part (spec #247 point 12). It is one thing for the whole ad: a yes covers the part as it stands, its looks and its photos, on every scene that shows it and has no clip yet, so later pictures and fixes of it need no new yes; a correction (new words or a new photo) changes it on every such scene, draws the one library item again and needs a new yes.
_Avoid_: Invented part, described part, part approval

### Not yet built

Named so the words mean one thing when these are built. Nothing implements them today.

**Side picture**:
A picture of the product alone on a plain background, showing one side the product photos show, drawn from that photo and checked against it, or the photo itself when it is already plain. Made once per ad, only when the product photos show the product from more than one side.
_Avoid_: Reference picture, side photo

**Promo code**:
A discount code the user supplies. Always a user-supplied fact, never taken from the page.
_Avoid_: Coupon, discount, offer

**Critic**:
The agent that runs the quality checks and returns a verdict. The producer calls it, for one scene or for the checks that look across scenes.
_Avoid_: Judge, reviewer, QA agent

**Director**:
An agent that would make one scene for the producer, seeing only that scene. Built only if one agent is measured not to be enough.
_Avoid_: Sub-agent, worker, renderer

**End card**:
A closing card carrying the call to action, such as "Shop now", drawn after the last scene. Not the call to action the person says in the last scene (see Part).
_Avoid_: CTA, outro, closing frame
