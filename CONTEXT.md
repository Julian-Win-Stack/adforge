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

**Quote**:
An exact chunk of the shop owner's own message that the producer passes with its instruction to a scene agent. Code checks the words against an owner message, ignoring spacing, quote marks and capitals, never words, and keeps which message it came from. The producer's messages, notices and pasted page text do not count as the owner's words.
_Avoid_: Summary, instruction, paraphrase

**Interrupt**:
A message that arrives while the producer is working. The producer decides what it means for work already in flight.
_Avoid_: Cancel, abort

**Pause**:
The shop owner's button that stops new work until they write again. Queued scene steps are cancelled for free; running steps finish and are kept. A running scene agent finishes its current action and parks, then resumes from its kept conversation and checkpoints. The producer is told what stopped in a system note and reads the owner's next message before starting more work; that message ends the pause.
_Avoid_: Stop, hold, cancel

### The agents

**Agent**:
A model in a loop that chooses which tool to call next. The producer handles the session; each B-roll scene has a scene agent that draws, checks and films it. The critic comes later.
_Avoid_: Bot, assistant

**Producer**:
The one agent the user talks to for the whole session (ADR 0010, ADR 0011). Reads the page, asks, plans, creates the person and music, makes talking scenes and assembles the ad. It starts every ready B-roll scene's agent together and passes later notes as its own instruction plus checked quotes of the owner's words, including while a scene is busy. It handles other edits and kept-version swaps itself; a card swap is made by code and reported to it. It tells the owner in one line what changed and cost. Only an owner part needs the owner's yes before making its clip.
_Avoid_: Orchestrator, main agent, "the agent"

**Scene agent**:
The agent that owns one B-roll scene for the ad's whole life in one chat (spec #303). It picks the photos, draws and fixes the starting picture, films the clip, reads the checks and fixes again. The producer starts it and passes later notes to the same chat; only the producer speaks to the shop owner.
_Avoid_: Director, fixing agent, worker

**Director**:
The earlier name for a proposed per-scene agent, once deferred until one producer was measured not to be enough. ADR 0011 replaces that deferred design with the built scene agent, whose persistent chat draws, checks, films and fixes one B-roll scene.
_Avoid_: Sub-agent, worker, renderer

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

**Run**:
One window of a scene agent's work, opened by the producer's instruction and the owner's checked quotes. It has its own caps of 6 starting pictures and 6 clips and ends when a clip passes, the agent gives up or its clips run out. A later note opens another run in the same scene's chat, including while the agent is busy; it keeps the earlier work in view.
_Avoid_: Session, fix, try

**Scene**:
One spoken line in a job, and everything produced for it. A scene has no planned length: it lasts exactly as long as its line takes to say.
_Avoid_: Shot, beat, clip, segment

**Talking scene**:
A scene in which the person says the line to camera. Every scene is one unless the plan gives it something to show, and the first scene always is.
_Avoid_: A-roll, presenter scene

**B-roll scene**:
A scene that shows the product or its problem while the person's voice says its line over it. The plan names its action, kind, who is in it, usage and result; these are fact checked like the line. Its scene agent picks photos, draws and checks a starting picture, then films and fixes the clip. The video model gets that picture alone, or that picture plus the planned piece of a page video. The owner sees the starting picture on the card without having to approve it.
_Avoid_: Cutaway, product shot, insert

**Scene step**:
One piece of a scene's work run in the background: a talking starting picture or clip, line audio, its transcript, or a B-roll scene agent's work. A producer tool starts it and returns at once; when it finishes or fails, the producer learns what happened and what it cost.
_Avoid_: Task, job, render

**Starting picture**:
The first frame a scene's clip is made from. For a talking scene it shows the presenter holding the product, drawn from the portrait and a product photo. A B-roll scene's agent draws it from photos it picks and the shared person-and-room picture, or the room alone for a scene without a face. A separate picture check answers before filming. The card shows the picture its clip was filmed from. A changed B-roll line, action, place, shared picture it used or picked photo makes the picture and clip stale.
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
A scene's moving picture with its line's audio, the same audio its transcript was heard in. A talking clip animates its starting picture speaking that audio. A B-roll clip animates its starting picture silently, after the picture-check stage, with a page-video piece as a movement reference when the plan names one; its voice is laid over it afterward. It lasts the fewest whole seconds covering the audio, at least 5 and at most 15, and plays whole so its motion finishes. Audio too long for any clip is shortened before filming, or said to camera after three failed shortenings. The scene agent reads each B-roll clip's judge answers and fixes in the same chat. Every filmed try is kept, shown and available on the card.
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
The words on the product page about the product this page sells, plus the structured product data the page declares for search engines. A model copies this product's passages out of the page word for word, and code keeps only the copied sentences it finds on the page, so text about other products sold alongside it ("pairs well with", bundles) and anything the model made up never gets in. The page's source of product facts; the owner's own words may also supply facts. Models read this, never the HTML. The whole visible text is kept beside it, for looking things up, but no model writes from it.
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
What a clip was sent: its mode, starting picture, any page-video piece and prompt. For B-roll it also keeps the photos and job lines the starting picture was drawn from, shown to the clip judge as real-product references but never sent to the video model. Older clips keep their original numbered reference pictures, tiers and recolour outcomes.
_Avoid_: References, inputs, pictures sent (the starting picture's old record)

### Fixing a scene

**Note**:
The shop owner's own words about a scene they were shown, such as "the tea should actually pour". The producer passes a B-roll note to that scene's same agent chat as an instruction plus checked quotes; one message about several scenes can give each scene only its own words. The producer handles other changes itself.
_Avoid_: Feedback, comment, complaint

**Fix**:
A scene agent's new run for an owner note, keeping its earlier conversation and work. The producer passes its instruction and checked quotes; the agent can change the scene, draw or edit a starting picture and film again. Each run has fresh picture and clip caps. The last clip stays in the ad while the fix runs and if it gives up; with no clip at all, the owner is offered a photo or saying it to camera. A question for a photo or video reaches the owner once while work carries on, and the answer reaches the same scene chat.
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
The clip judge's extra question for a run that changes an earlier clip: is the requested change clearly present? It sees the earlier clip beside the new one and gets the captured run's producer instruction and checked owner quotes. A "not fixed" answer fails the try and the scene agent reads why. If its answer cannot be had, the owner is told to look at the clip; the check never waits for a yes.
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
Library items the owner does not want used for one scene. The producer sets it from the owner's words, and the scene agent cannot pick an excluded photo or its versions. It stays in force until changed.
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
A check on something already produced, switched on or off as a whole. The B-roll picture check answers whether the product matches its photos and whether anything breaks physics. The separate clip judge sees the starting picture, its picked product photos with their job lines, then the clip; it checks references, product and motion and keeps its ruling. The talking-picture check remains separate. With quality checks off, the scene agent accepts its picture and stops after its first clip.
_Avoid_: QA, review, gate

**Picture check**:
The separate check on a B-roll starting picture (spec #303). It gives two one-line answers, seeing the same picked photos and lines as the picture model, the product's name and colour, the page's words and the owner's chat; it sees nothing of the scene agent's chat, reasons or prompt. The scene agent reads its answers and chooses how to fix a failed picture.
_Avoid_: Clip judge, talking picture check, scene agent

**Auto-fix**:
The scene agent's ordinary continuation after the picture check or clip judge fails its work, in the same chat that made the first try. It chooses an edit, redraw, changed scene or another filming. A run can draw six pictures and film six clips. After six failed pictures it may film the failed picture it names as closest, with its reason, and the owner is told. Six failed clips end the run with the last try in the ad and every try available on the card. No owner approval is needed to keep trying; an uncheckable picture or clip is kept unchecked and the owner is told to look.
_Avoid_: Retry, redo (a redo is made for a fix), round (a round is one batch of owner notes)

**Clip version picker**:
A scene card's strip of every clip the scene was shown, tries included, numbered as its versions, each badged with what the clip check said (passed, failed, not checked), the one in the ad outlined (Julian, Oct 29). A tap previews a clip; "Use this in the ad" swaps it in by code for nothing, with Undo, and the ad is assembled again by itself. A clip that no longer says the scene's line is faded, tagged "Old line", and can't be swapped in. The one place the shop owner picks a clip.
_Avoid_: Previous/Latest, "Use this one"

**Try**:
One clip the scene agent films in a run, including its first clip. Each has the starting picture it was filmed from and the judge's answers. Every try is a version the owner can watch and pick. A new owner note starts a new run with fresh caps, without losing earlier tries.
_Avoid_: Round, remake, attempt

**Version**:
One scene's kept starting picture and clip as the owner was shown them, numbered from 1 in the order shown. A picture starts a version; filmed clips keep the picture they came from, and each clip try can be picked on the card. A version brought back by going back becomes the newest version. A kept clip that still says the scene's current line can be swapped into the ad for free.
_Avoid_: Variant (a variant is another job), copy

**Reference photo**:
A photo the owner attaches to show how something in a scene should look. It enters the library cleaned and labelled; an attached video is watched into moments and look frames. The producer passes the owner's words and the new library items to the scene agent, which chooses the photos for its drawing. The owner's appearance corrections beat the page. Reference photos are never sent directly to the video model for new B-roll clips.
_Avoid_: Reference picture, owner picture

**Reference mode**:
The video model's mode for a B-roll scene with a planned page-video moment: the starting picture is Image 1, with the cut piece of that video as the movement reference. The prompt says the clip starts exactly like Image 1 and what to take from the video. Product photos are drawing inputs, not video inputs. Without a usable piece, the clip uses start-frame mode: only the starting picture, no end frame or reference photos.
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

**End card**:
A closing card carrying the call to action, such as "Shop now", drawn after the last scene. Not the call to action the person says in the last scene (see Part).
_Avoid_: CTA, outro, closing frame
