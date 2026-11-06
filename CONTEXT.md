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
A model in a loop that chooses which tool to call next. The producer handles the session; each scene has a scene agent that draws, checks and films it. The critic comes later.
_Avoid_: Bot, assistant

**Producer**:
The one agent the user talks to for the whole session (ADR 0010, ADR 0012). Reads the page, asks, plans, creates the person and music, starts every ready scene agent together and assembles the ad. It passes later notes as its instruction plus checked owner quotes, including while a scene is busy. It handles edits and kept-version swaps; code reports card swaps to it. It tells the owner what changed and cost. An owner part needs their yes before filming; changing the voice needs their agreement to the cost of filming every scene again.
_Avoid_: Orchestrator, main agent, "the agent"

**Scene agent**:
The agent that owns one scene, talking or B-roll, for the ad’s whole life in one chat (ADR 0012). It picks photos, draws and checks a starting picture, films with H3 speaking the checked line, reads the word check and clip judge, and fixes again. The producer starts it and passes later notes to the same chat; only the producer speaks to the owner.
_Avoid_: Director, fixing agent, worker

**Director**:
The earlier name for a proposed per-scene agent, once deferred until one producer was measured not to be enough. ADR 0011 replaces that deferred design with the built scene agent, whose persistent chat draws, checks, films and fixes one scene of either kind (ADR 0012).
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
One spoken line in a job and everything produced for it. Its explicit kind is talking or B-roll. Each new clip plays whole, including an action after the last word.
_Avoid_: Shot, beat, clip, segment

**Talking scene**:
A scene in which the presenter speaks to camera while showing, using or showing the result of the product, her face in frame. The first scene is always talking. Showing the product to the lens while talking is the last fallback when movement keeps failing.
_Avoid_: A-roll, presenter scene

**B-roll scene**:
A scene showing hands and product, with the presenter’s face out of frame and her H3 voice speaking off camera. The plan names its action, usage, result and why her hands cannot show the line at arm’s length. These are fact checked like the line. Its scene agent draws and checks the starting picture, films, reads both checks and fixes. The owner sees its picture without approving it, except for an owner part.
_Avoid_: Cutaway, product shot, insert

**Scene kind**:
Talking or B-roll, chosen explicitly for each scene. Talking means the presenter speaks on
camera while showing, using or showing the result of the product. B-roll means hands and
product only, with her face out of frame and her voice off camera inside the H3 clip.
_Avoid_: Type, has face, reading the kind from the action

**Talking style**:
How a talking scene films the presenter, one of a fixed list: selfie, propped waist-up, seated
close-up, or talking while doing it. The planner names it for each talking scene (spec #401).
_Avoid_: Pose, shot type

**Product state**:
Where the product is in a talking scene, one of a fixed list: in its box, out of the box (the
product alone, no box shown), in use, or beside its box. A box state names the page photo of
the packaging (spec #401).
_Avoid_: Product position, prop state

**Proof**:
What the viewer sees in a scene that proves its line, written by the planner for every scene
(spec #401). A plan with a scene that has none is sent back. The **Plan quality check** then
asks whether each scene's action shows it, but not of a first or last scene that only shows the
product to the lens; every B-roll scene shows proof of its line, and a still product shot passes
only when the line is about exactly that. Each scene agent gets its scene's proof beside its line.
_Avoid_: Evidence, demo

**Kind's why**:
The planner's explanation of its scene-kind choice, required for B-roll: what the presenter's
hands cannot show at arm's length. Kept on the scene and shown where Julian grades and in the
round export, never to the shop owner. Separate from a tool call's **Why**.
_Avoid_: Owner explanation, tool reason

**Voice words**:
The description of how the presenter's voice sounds, reused unchanged in every H3 clip of the
ad. Words sent with the checked line, not a designed voice file or an audio sample.
_Avoid_: Voice ID, voice sample, line audio

**Word check**:
Comparing the clip's transcript words with its checked line. Code tidies capitals, punctuation
and numbers first; an exact match passes without a model. A difference goes to a small Azure
text model: a changed, missing or extra name, number, price or claim fails; a difference a
listener would not notice passes. No answer means unchecked, with a notice asking the owner
to listen while the ad continues. Every check is recorded with its line, heard words, verdict
and whether code or the model settled it; these records are not failures that page Sentry.
_Avoid_: Fact check, lip-sync check, clip judge

**Scene step**:
One piece of scene work in the background: its scene agent’s work, or a clip and its transcript made inside that work. A producer tool starts the agent and returns at once; the producer learns how its run ended and what it cost. Historical steps also include line audio and producer-made talking pictures and clips.
_Avoid_: Task, job, render

**End picture**:
The frame a clip must end on. It is an edit of the clip's starting picture that shows where the clip's one action ends: its result at the spot the product touched, a crop of the real label a push-in ends on, the place the clip must end, or the set point the action stops at. It is not a second state: a two-state claim is still two clips joined by a cut (proven rule).
_Avoid_: End frame, last frame, ending pic, keyframe.

**Starting picture**:
The first frame a scene’s clip is made from. Its scene agent draws it from picked product photos and the shared person-and-room picture for talking, or room alone for B-roll. A separate picture check answers before filming. The card shows the picture its clip was filmed from. A changed line, action, place, shared picture it used or picked photo makes it stale.
_Avoid_: Start frame, keyframe, thumbnail

**Before-picture**:
One picture per ad of the place its B-roll scenes happen in, before any of them starts: each thing the scenes act on, with every problem any of them shows, and no product or person. Drawn once, when the ad's room alone is first needed. Its words describe the room the shared picture draws the presenter in, so the scenes match.
_Avoid_: Base picture, background, set

**Shared picture**:
One picture per ad of the presenter in the before-picture's room, relaxed, nothing else happening. Drawn in one go from her portrait (for her face only) and words for her, her clothes and the room, never by merging a finished portrait with a finished room, so she doesn't look pasted on. Every talking starting picture is an edit of it. The room alone for B-roll is this picture with her taken out. A scene with its own place gets its own, drawn the same way.
_Avoid_: Person-in-room merge, composite

**Line's audio**:
The separately spoken line kept for ads made before the H3 switch. It and its transcript remain readable with old clips; new clips have their voice made by H3 inside the clip and need no advance audio.
_Avoid_: Voiceover, recording, clip

**Transcript**:
What was heard in a clip’s own sound, word by word with when each was said, exactly as spoken: nothing tidied or matched to the line. Kept with that clip. Historical transcripts remain with their line audio. Transcription still runs with quality checks off, for captions.
_Avoid_: Captions, subtitles

**Caption**:
A few of a line’s written words drawn along the bottom while they are said, above the bottom 380 px TikTok and Reels cover, timed from the transcript of its clip. Captions are omitted when a clip could not be transcribed; the ad continues.
_Avoid_: Subtitle, lower third

**Overlay**:
The on-screen text for one scene, such as the price, written by the producer when it plans the ad; a scene may have none. Drawn in a fixed band along the top while the scene plays, below the top 220 px TikTok and Reels cover, clear of the face and the product. Not fact checked yet.
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
A scene’s moving picture and H3’s own sound. Code inserts its checked line exactly into speech tags, with the same voice words in every scene, on camera for talking and off camera for B-roll. Its scene agent films from the checked starting picture, optionally with a planned page-video piece. Code sets 5–15 seconds from the line at 2.5 words a second, rounded up plus a second of action after her last word. It plays whole. Both word check and clip judge run; either failure fails the try. Unavailable word checks leave it unchecked and the ad continues. Every try is kept on the card. Old clips keep their original sound and lineage.
_Avoid_: Video, render, shot

**Finished ad**:
Every scene’s clip joined in order with its own sound, whole, with written captions timed by its transcript, overlays and music underneath at the fixed volume. No voice overlay, early cut or lip-match skip runs for H3 clips. A wholly historical ad retains its original assembly; once a new H3 clip is added, retained and new clips play whole together. It ends on its end card. Shown in the chat and kept as a new version when clips, overlays, music or the end card change.
_Avoid_: Final video, render, output

**End card**:
The card a finished ad ends on after its last scene, drawn by our own edit (no paid call) and held 2 seconds over the music alone (spec #401: at least 1.5 s). Inside the safe area, it shows the product photo large, the product's name, one line in the page's own words, the price only when the plan gives one, and the website from the product's link. The planner writes the plan's brand, end card line and end card price; code sends back a plan whose brand isn't in the page text or the last line, whose end card line isn't the page's own words, or whose price isn't on the page, and one with a price while the price question is unanswered. Its time counts toward the ad's length: the 35-second cap and the owner's target include it. No brand logo: the photo and name do that job. Not the call to action the person says in the last scene (see Part).
_Avoid_: CTA, outro, closing frame

**Person**:
The presenter in an ad: a portrait and voice words reused in every H3 prompt. The plan’s gender goes into portrait and voice descriptions. No advance voice sample is made; the owner hears it in the first clips. A voice change needs their agreement to the cost and every scene filmed again because the voice is baked in. H3’s tone, pace and loudness may drift between clips.
_Avoid_: Avatar, actor, character, persona

**Product size**:
How big the product is, judged by the plan from the photos: tiny (fits on a fingertip: earrings, earbuds), handheld (held in one or two hands: a bottle, a rolled mat) or large (can't be held: a chair, a treadmill). Decides the pose.
_Avoid_: Scale, dimensions, category

**Pose**:
How the presenter is placed with the product in a scene’s starting picture. The scene agent describes an action appropriate to the planned kind, product size and picked photos. Talking scenes may show or demonstrate it while speaking; captions and overlays keep their clear bands.
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
A video the shop placed with the product photos in the product's gallery, picked off the page with them; reviews, creator videos and banners are not product videos. Kept with the job as its file with its first frame, frames spread over it and its length; a video in an embedded player (Vimeo, Wistia, Vidyard and any other yt-dlp reads) is downloaded from it, at most 720p with its sound, and kept the same way, except a YouTube one; when no file can be had (a YouTube player, a player that won't download, a stream, a dead link) as its link, with the page's poster or the player's thumbnail as its first frame. A page's videos are fetched at the same time. A page read again only adds the videos the job doesn't have yet, so each is watched once. Every file up to 3 minutes long is watched by the **Watcher**, all at once, before the plan: its **Moments**, a **Look frame** per product state (into the **Library**), the product's colour, and the **Transcript of a product video** (the words said in it, as the watcher heard them; "music only" when nothing is said). One the watcher says doesn't show this product is thrown out, and the owner is told in grey; one over 3 minutes isn't watched, and the owner is told which. The planner and the fact check get every transcript as the shop's own words, like the page text.
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
The three general refusals of a wish in agents/hard_list.py, each a tool answer: a new product mid-ad, a clip of an invented part without the shop owner's yes, and the video API's limits. Voice-change cost consent is the explicit ADR 0012 exception; other new guards follow graded failures.
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
The recorded rate of a separately designed historical voice. New ads use one fixed estimate of 2.5 words a second for line limits, target fit, clip length and music estimates; no measuring call is made.
_Avoid_: Pacing, WPS, rate

**Replay**:
Round 4's ads made again with every starting picture and clip taken from what round 4 saved, and their voice and music faked (evals/owner_notes), so shop owners' notes can be sent to them for the price of the text models.
_Avoid_: Fixture ad, test ad

### Checks

**Planning check**:
A check that looks only at text, before anything is rendered. The plan quality check, the fact check and the length fit. Always runs, including when quality checks are switched off.
_Avoid_: Validation, pre-flight, gate

**Plan quality check**:
A planning check that reads a plan once it passes the code rules: for each scene, whether its action shows its **Proof**; whether any claim is said twice; whether the hook is the page's strongest true point. A "no" sends the plan back to the planner with its reasons, at most twice; when no plan passes, the one with the fewest "no" answers goes on. No answer never blocks. Every verdict is recorded for the developer.
_Avoid_: Plan judge, plan review

**Fact check**:
Comparing every price, number, product name and claim in a line against the page text. A claim the page does not state counts as not matching. A line that has not passed cannot be rendered.
_Avoid_: Verification, accuracy check

**Quality check**:
Checks on produced work, switched on or off together: starting-picture checks, the product/motion clip judge and the word check. Every kind uses its scene agent’s picture check. With checks off the agent accepts its first picture and clip, but transcription still runs for captions.
_Avoid_: QA, review, gate

**Picture check**:
The separate check on each scene’s starting picture. It gives product-match and physics answers, seeing the picked photos and their job lines, product name and colour, page words and owner chat. It sees none of the scene agent’s chat, reasons or prompt. The agent chooses how to fix a failed picture.
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
