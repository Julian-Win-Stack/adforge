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
What the code (not the producer) records when a step fell back or failed, or tells the user they can act on. Its kind decides who sees it: the chat shows only what the user must act on or a reply to what they just did; every other kind goes only to the **developer record**. Every notice is kept as one of the job's warnings and never given to a model.
_Avoid_: error message (for a fallback the user can't act on)

**Developer record**:
Where every notice is kept for the developer: the job's warnings and Sentry, one issue per kind of failure.

**Nudge**:
A note code gives the producer, never shown to the user, when it went to stop with the ad unfinished and nothing running or waiting on the user: carry on, or say why you can't. At most 2 per user message.

**Attachment**:
A file a message carries: a photo the user attached, or a picture, sound or video the producer made. A scene's starting picture or clip carries its scene's number.
_Avoid_: Media, upload, asset

**Brief**:
What the user asks for in their own words: the link, the length, a promo code, anything else. Not a form and not a stored record.
_Avoid_: Request, prompt, order

**Quote**:
An exact chunk of the shop owner's own message that the producer passes with its instruction to a scene agent. Code checks it against an owner message and keeps which message it came from.
_Avoid_: Summary, instruction, paraphrase

**Interrupt**:
A message that arrives while the producer is working. The producer decides what it means for work already in flight.
_Avoid_: Cancel, abort

**Pause**:
The shop owner's button that stops new work until they write again. Queued scene steps are cancelled; running steps finish and are kept; a running scene agent parks and later resumes from its checkpoints. The owner's next message ends the pause.
_Avoid_: Stop, hold, cancel

### The agents

**Agent**:
A model in a loop that chooses which tool to call next: the producer, and one scene agent per scene.
_Avoid_: Bot, assistant

**Producer**:
The one agent the user talks to for the whole session. Reads the page, asks, plans, creates the person and music, starts scene agents and passes them later notes, assembles the ad and tells the owner what changed and cost.
_Avoid_: Orchestrator, main agent, "the agent"

**Scene agent**:
The agent that owns one scene, talking or B-roll, for the ad's whole life in one chat. It picks photos, draws and checks a starting picture, films, reads the word check and clip judge, and fixes. Only the producer speaks to the owner.
_Avoid_: Director, fixing agent, worker

**Look**:
A call the producer makes on its own model to see a picture, a clip's frames, a version, a library item or a sent list. It answers in words only (what is wrong, root cause, a direction) and decides nothing, so the producer's conversation never holds pictures.
_Avoid_: Decider, critic, look_at

### The work

**Job**:
One ad being made. A session holds as many as the user asks for.
_Avoid_: Ad, run, task, order

**Variant**:
A job that points at another job in the same session as the one it varies, so the two can be compared. Variants are siblings, not versions of each other.
_Avoid_: Version, copy, alternative

**Run**:
One window of a scene agent's work, opened by the producer's instruction and the owner's checked quotes, with its own caps on starting-picture tries, end-picture tries and clips. It ends when a clip passes, the agent gives up or its clips run out. A later note opens another run in the same chat.
_Avoid_: Session, fix, try

**Scene**:
One spoken line in a job and everything produced for it. Its explicit kind is talking or B-roll.
_Avoid_: Shot, beat, clip, segment

**Talking scene**:
A scene in which the presenter speaks to camera, face in frame, while showing, using or showing the result of the product. The first scene is always talking. Each names a **Talking style** and a **Product state**.
_Avoid_: A-roll, presenter scene

**B-roll scene**:
A scene showing the product with any part of the presenter's body but never her face, her voice speaking off camera. A worn product copies a page photo's outfit and how it is worn.
_Avoid_: Cutaway, product shot, insert

**Scene kind**:
Talking or B-roll, chosen explicitly for each scene. Middle scenes are B-roll by default, and every ad of two or more scenes has at least one B-roll scene.
_Avoid_: Type, has face, reading the kind from the action

**Talking style**:
How a talking scene films the presenter: selfie (she holds the phone at arm's length), propped (phone set down, both hands free), or talking while doing it (using the product on herself, filmed propped). A B-roll scene has none.
_Avoid_: Pose, shot type

**Product state**:
Where the product is in a talking scene: in its box, out of the box, in use, or beside its box. Never the same as the previous talking scene's. A B-roll scene has none.
_Avoid_: Product position, prop state

**Kind's why**:
The planner's explanation of its scene-kind choice, required for B-roll: what the presenter's hands cannot show at arm's length. Shown where Julian grades, never to the shop owner. Separate from a tool call's **Why**.
_Avoid_: Owner explanation, tool reason

**Voice words**:
The description of how the presenter's voice sounds (warm, natural, chatty, age, casual accent), sent with the checked line in every clip. Describes a voice, not a person or an ad. Not a voice file or audio sample.
_Avoid_: Voice ID, voice sample, line audio

**Word check**:
Comparing a clip's transcript with its checked line: would a viewer hear something different from the line? A changed, missing or added fact, garbled extra words or a cut-off ending fails. No answer means unchecked, and the owner is asked to listen.
_Avoid_: Fact check, lip-sync check, clip judge

**Scene step**:
One piece of scene work in the background: a scene agent's work, or a clip and its transcript made inside it.
_Avoid_: Task, job, render

**End picture**:
The frame a clip must end on: an edit of its starting picture showing where the clip's one action ends. Not a second state.
_Avoid_: End frame, last frame, ending pic, keyframe.

**Starting picture**:
The first frame a scene's clip is made from, drawn from picked product photos and the shared picture (talking) or the room alone (B-roll), and checked before filming.
_Avoid_: Start frame, keyframe, thumbnail

**Before-picture**:
One picture per ad of the place its B-roll scenes happen in, before any of them starts: every thing they act on, with its problems, and no product or person.
_Avoid_: Base picture, background, set

**Shared picture**:
One picture per ad of the presenter in the before-picture's room, relaxed, drawn in one go from her portrait and words, never by merging a portrait with a room. The room alone for B-roll is this picture with her taken out.
_Avoid_: Person-in-room merge, composite

**Line's audio**:
The separately spoken line of ads made before the H3 switch. New clips get their voice from H3 inside the clip.
_Avoid_: Voiceover, recording, clip

**Transcript**:
What was heard in a clip's own sound, word by word with timings, exactly as spoken. Kept with that clip; runs even with quality checks off, for captions.
_Avoid_: Captions, subtitles

**Caption**:
A few of a line's written words drawn along the bottom while they are said, timed from its clip's transcript.
_Avoid_: Subtitle, lower third

**Overlay**:
The on-screen text for one scene, such as the price, written by the producer when it plans; drawn in a band along the top. A scene may have none.
_Avoid_: Caption, graphic, text layer, on-screen text

**Script format**:
The structure an ad's body plays in, between hook and call to action: problem-agitate-solve, feature cascade, before and after, or day in the life. One per ad.
_Avoid_: Template, framework, ad format

**Part**:
What a scene does in the script: the hook first, the call to action last, the script format's parts between. Each is one scene.
_Avoid_: Beat, section, stage

**Second state**:
A B-roll scene showing the second of two end states one claim needs (a second way of use, or proof seen only after an action), playing just after the scene showing the first.
_Avoid_: Pair, variant, alternate use

**Clip**:
A scene's moving picture with H3's own sound, speaking its checked line, filmed from the checked starting picture. Both word check and clip judge run on it.
_Avoid_: Video, render, shot

**Video form**:
The scene agent's descriptions that code joins into the prompt H3 receives (`backend/agents/scene_forms.py:VideoForm`). Code owns the speech, voice words and fixed lines.
_Avoid_: Spoken script, finished prompt

**Finished ad**:
Every scene's clip joined in order with its own sound, captions, overlays and music, ending on its end card. Kept as a new version when any of these change.
_Avoid_: Final video, render, output

**End card**:
The card a finished ad ends on after its last scene, drawn by our own edit: product photo, name, one line in the page's own words, the price if the plan has one, and the website. Counts toward the ad's length. Not the call to action the person says.
_Avoid_: CTA, outro, closing frame

**Person**:
The presenter in an ad: a portrait and voice words reused in every clip prompt. A voice change means filming every scene again.
_Avoid_: Avatar, actor, character, persona

**Product size**:
How big the product is, judged from the photos: tiny (fits on a fingertip), handheld, or large (can't be held). Decides the pose.
_Avoid_: Scale, dimensions, category

**Pose**:
How the presenter is placed with the product in a scene's starting picture.
_Avoid_: Framing, composition, blocking

**Page text**:
The words on the product page about the product this page sells, plus its structured product data, copied word for word by a model and kept only where found on the page. The source of product facts; models read this, never the HTML.
_Avoid_: Page content, scraped text, HTML

**Product photo**:
A photo of the product this page sells, kept with the job and picked off a numbered screenshot of the page; scenes are made from them. Photos of other products on the page are left out.
_Avoid_: Image, picture (for these), asset

**Hidden picture**:
A picture on the product page that the screenshot can't show because a scroll box or carousel clips it; shown to the photo picker beside the screenshot.
_Avoid_: Off-screen image, lazy image

**Product video**:
A video the shop placed in the product's gallery; reviews, creator videos and banners are not. Watched by the **Watcher** before the plan; its transcript counts as the shop's own words.
_Avoid_: Media, asset, clip (a clip is a made ad clip)

**Video picture**:
A product photo cut from a product video at a look frame the watcher named. Counts as a product photo (looks only); never a reference photo.
_Avoid_: Frame, still, keyframe, video frame

**Page panel**:
The panel in the chat where the shop owner pastes the page's text and attaches its photos, shown when the page can't be read.
_Avoid_: Shed, form, manual mode

**Owner-pasted page**:
Page text the shop owner pasted in the page panel, kept and used as the page text.
_Avoid_: Override, manual input, pasted facts

**User-supplied fact**:
Something the user states that the page does not, such as a promo code or a replacement line. Used as written and not fact checked.
_Avoid_: Override, manual input

### The library

**Library**:
Everything the app knows about one product's pictures and videos: kept page photos, cleaned owner photos, watched page videos, product-only and recoloured versions.
_Avoid_: Asset pool, gallery, media

**Labelling call**:
The call that gives a photo the picker never saw (an owner photo, or one the page only declares) the picker's fields and label.
_Avoid_: Tagger, classifier

**Library number**:
An item's number in the library, fixed for the ad, counting from 1 in the order items enter. The planner names every picture by it.
_Avoid_: Index, photo number (a product photo's position is another number)

**Library sheet**:
One picture the planner is shown holding several library pictures, each with its number and label under it.
_Avoid_: Grid (the picker's), contact sheet

**Look picture**:
A picture whose job is to show the product's look (a side or a state).
_Avoid_: Side picture, reference picture

**Scene picture**:
A picture one scene needs (content, result, in use).
_Avoid_: Reference photo (the owner's photo on a note)

**Piece**:
A 2-15 s cut of a page video sent as a reference video.
_Avoid_: Clip (a clip is a made ad clip), segment

**Moment**:
One action in a page video, with its start and end seconds, numbered per video from 1. A piece is named by its moment's number.
_Avoid_: Scene, shot, video picture

**Watcher**:
The model that watches a page video whole, with its sound, once per video. It says whether the video shows this product and writes its moments, look frames, the product's colour and its transcript.
_Avoid_: Video reader (the old one)

**Look frame**:
A frame of a page video the watcher named as the best view of one product state, kept in the library.
_Avoid_: Video picture (its product-photo copy)

**Sent list**:
What a clip was sent: its mode, starting picture, any page-video piece and prompt (for B-roll, also the photos the starting picture was drawn from, shown only to the clip judge).
_Avoid_: References, inputs, pictures sent

### Fixing a scene

**Note**:
The shop owner's own words about a scene they were shown, such as "the tea should actually pour". The producer passes it to that scene's agent as instruction plus checked quotes, or handles other changes itself.
_Avoid_: Feedback, comment, complaint

**Fix**:
A scene agent's new run for an owner note, keeping its earlier conversation and work. The last clip stays in the ad while it runs and if it gives up.
_Avoid_: Change request, edit, revision

**System note**:
What code tells the producer in a short sentence in its conversation: a step finished or failed, a fix done or parked, a pause, a version swapped in, a stale mark, an error, the new ad shown. Never a picture. Not shown to the owner.
_Avoid_: Producer message, event

**Fix check**:
The clip judge's extra question for a run that changes an earlier clip: is the requested change clearly present? "Not fixed" fails the try.
_Avoid_: Fix judge, verification, QA

### Machinery

**Tool**:
One thing an agent can do, such as reading the page or making a scene's clip. The agent chooses which to call and when; the tool enforces the rules that matter.
_Avoid_: Step, action, function, capability

**Tool answer**:
What a tool hands the producer: the thing it made, "can't, because ..." with the reason, or one clarifying question. None stops the run.
_Avoid_: Error, refusal, result

**Hard list**:
The general refusals of a wish in `agents/hard_list.py`, each a tool answer: a new product mid-ad, a clip of an invented part without the owner's yes, and the video API's limits.
_Avoid_: Guards, rules, blocks

**Stale**:
A scene step whose inputs changed since it ran. Code marks it and says so; the producer decides whether to rerun it. Code never reruns on its own.
_Avoid_: Dirty, outdated, invalid

**Fold**:
Replacing the oldest tool calls in the producer's conversation by one line each once it passes half the model's window. Nothing is deleted from the record.
_Avoid_: Summary, truncation, compaction

**Exclude list**:
Library items the owner does not want used for one scene; the scene agent cannot pick them or their versions.
_Avoid_: Blocklist, banned pictures

**Why**:
The one sentence the producer writes with every tool call, stored with the call and shown on the per-ad page of what the agent did.
_Avoid_: Reason, rationale

**Checkpoint**:
The record of a finished tool call: tool, arguments, output, cost. What a restart resumes from.
_Avoid_: State, snapshot, progress marker

**Gateway**:
The single place every model call passes through: validates the handoff, retries, records the call with its cost, and answers an already-paid call from its record.
_Avoid_: Client, wrapper, adapter, provider

**Handoff**:
The validated data passed into a model call, and the validated shape expected back. Checked before money is spent.
_Avoid_: Payload, request body, contract

**Model call**:
One attempt at one model, recorded with its cost, duration, outcome and reason.
_Avoid_: API call, request, generation

**Replay**:
Making an ad again from the saved output of an earlier eval stage, so only the later stages run and are paid for. The owner-note replay remakes saved round ads from their saved pictures and clips, voice and music faked (`evals/owner_notes`), so owner notes cost only the text models.
_Avoid_: Fixture ad, test ad

### Checks

**Planning check**:
A check on text only, before anything is rendered: the fact check and the length fit. Always runs.
_Avoid_: Validation, pre-flight, gate

**Fact check**:
Comparing every price, number, product name and claim in a line against the page text. A claim the page does not state does not match. A line that has not passed cannot be rendered.
_Avoid_: Verification, accuracy check

**Quality check**:
Checks on produced work, switched on or off together: the picture check, the clip judge and the word check.
_Avoid_: QA, review, gate

**Picture check**:
The separate check on a scene's starting picture for product match and physics. It sees none of the scene agent's chat, reasons or prompt.
_Avoid_: Clip judge, talking picture check, scene agent

**Auto-fix**:
The scene agent's own next try after the picture check or clip judge fails its work, in the same chat, within the run's caps. Needs no owner approval.
_Avoid_: Retry, redo (a redo is made for a fix), round (a round is one batch of owner notes)

**Clip version picker**:
A scene card's strip of every clip the scene was shown, each badged with its check result, the one in the ad outlined. "Use this in the ad" swaps it in for free. The one place the owner picks a clip.
_Avoid_: Previous/Latest, "Use this one"

**Try**:
One clip the scene agent films in a run. Every try is a version the owner can watch and pick.
_Avoid_: Round, remake, attempt

**Version**:
One scene's kept starting picture and clip as the owner was shown them, numbered from 1 in the order shown.
_Avoid_: Variant (a variant is another job), copy

**Reference photo**:
A photo the owner attaches to show how something in a scene should look. It enters the library cleaned and labelled; never sent directly to the video model.
_Avoid_: Reference picture, owner picture

**Reference mode**:
The video model's mode for a B-roll scene with a planned page-video moment: the starting picture plus the cut piece as movement reference. Without one, the clip uses start-frame mode.
_Avoid_: Reference picture mode, way 3

**Owner part**:
A part of the product no photo shows, from the owner's words or invented by the planner. It gets its own library picture, which the owner must approve in chat before any clip showing it is made. One yes covers the part on every scene; a correction needs a new yes.
_Avoid_: Invented part, described part, part approval

### Evals

**Eval question**:
One yes/no about what the owner sees, for example "is the product in this clip the real one?". Answered by code, by a judge that agrees with Julian's grades, or by Julian.
_Avoid_: Outcome, rule, check (a check is the app's own guard)

**Case**:
One answer to one eval question that Julian graded, saved with its inputs.
_Avoid_: Example, sample, test

**Regression case**:
A case that passed and must keep passing.
_Avoid_: Locked outcome, golden case

**Capability case**:
A case the agent fails today; together they are the to-do list.
_Avoid_: Regression case, hard case

**Regression eval**:
Every scenario run on a new version of the app and compared with the baseline, to show nothing that passed now fails. Passes close to 100%.
_Avoid_: Test round, exam, benchmark

**Scenario**:
One product plus one situation (a page with only two photos, a see-through bottle, an owner note with a photo), replayed the same way every time: the same saved page, the same first message, the same owner notes in the same order.
_Avoid_: Test product, fixture, test case

**Full scenario**:
A scenario made all the way to a finished ad.
_Avoid_: Full run, long scenario

**Picture scenario**:
A scenario made only up to its starting pictures; no clip is filmed.
_Avoid_: Short scenario, cheap scenario

**Eval stage**:
One of the six points in making an ad where an eval looks at what was made: page read, plan, starting pictures, clips, finished ad, owner note. Each eval question is asked at the earliest stage where its answer shows.
_Avoid_: Place, step (a scene step is background scene work), checkpoint (a checkpoint is a finished tool call's record)

**Baseline**:
The last graded run of every scenario that passed the ship rule; a new version is compared with it. The first one is graded on the app as it is when the scenarios are chosen. A run that fails never becomes the baseline.
_Avoid_: Reference run, golden run, old version

**Ship rule**:
What a new version must meet before it is trusted: no regression case fails, and Julian, watching old and new side by side, finds the new no worse. A change meant to change an answer rewrites that case first, so it does not count as a failure.
_Avoid_: Release gate, quality bar

**Test round**:
A batch of new ads, usually on new products, that Julian grades to find what the app fails.
_Avoid_: Round (a round is one batch of owner notes), exam
