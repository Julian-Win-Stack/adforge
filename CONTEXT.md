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
A message the code posts, not the producer, when a step fell back to a worse source or failed. It says what failed, what was used instead, and what that means for the ad. Shown red when it affects the ad, grey when it is only worth knowing. Kept with the job as one of its warnings, and never given to a model. A scene's starting picture or clip also gets a notice when it starts being made ("Making scene 2's clip.") and when it ends with nothing made, saying why in plain words ("Scene 3's starting picture couldn't be made: an outside service stayed down."): these tell the chat which scene is being made, and aren't warnings.
_Avoid_: Alert, error message, system message

**Attachment**:
A file a message carries: a photo the user attached, or a picture, sound or video the producer made. Kept through the file store and shown in the chat. A scene's starting picture or clip carries its scene's number.
_Avoid_: Media, upload, asset

**Brief**:
What the user asks for in their own words: the link, the length, a promo code, anything else. Not a form and not a stored record of its own.
_Avoid_: Request, prompt, order

**Interrupt**:
A message that arrives while the producer is working. The producer decides what it means for work already in flight.
_Avoid_: Cancel, abort, pause

### The agents

**Agent**:
A model in a loop that chooses which tool to call next. Two are built: the producer, and the decider, which plans owner-note fixes; the critic comes later.
_Avoid_: Bot, assistant

**Producer**:
The agent the user talks to. Reads the page, asks, plans, creates the person and the music, makes each scene's first version and assembles the ad. The only agent that speaks in the chat. Once the ad has a clip it makes nothing: the decider plans the shop owner's notes and code runs the plan; the producer answers what isn't a note, from the system notes code writes, and never asks for a yes.
_Avoid_: Orchestrator, main agent, "the agent"

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
A scene that shows the product rather than the person talking, such as a pan sizzling, while the person's voice says the line over it. What it shows is written in plain words in the plan, with its kind, who is in it, its usage, its result and the photos it needs (its B-roll details). What it shows, its usage and its result are fact checked like the line, and a scene that fails is rewritten whole, B-roll details included. Its clip is made by Boreal-H3 (on Creatify) from a starting picture, never from shop photos: sent to the video model, a photo's own scene leaked into the clip. A photo of each thing the plan says it needs goes to the picture model instead, with its one job ("only how the gel's colour looks"). Made with the same tools as a talking scene. The user is never told which scenes are which, except when a B-roll scene has to become a talking scene: a notice says so, with the reason.
_Avoid_: Cutaway, product shot, insert

**Scene step**:
One piece of a scene's work, run in the background: its starting picture, its line's audio, the audio's transcript, and its clip. A scene tool starts it and returns at once; when it finishes or fails, the producer is told on its next turn and tells the user.
_Avoid_: Task, job, render

**Starting picture**:
A scene's first frame, made from the portrait and one product photo: the person holding the product, or, for a B-roll scene, the "before" of what the scene shows, made from its main photo, then a photo for each thing it needs that the main photo can't show (at most 3), then the portrait only when the presenter's face is shown, then the ad's before-picture. A B-roll scene's picture has a quality check before its clip is paid for, and is drawn again, at most twice, when it fails; when the third fails too, the run stops and the chat shows the three pictures and why each failed. The clip is made from it: a talking clip as its first frame, a B-roll clip in reference mode, told to start like it.
_Avoid_: Start frame, keyframe, thumbnail

**Before-picture**:
One picture per ad of the place its B-roll scenes happen in, before any of them starts: each thing the scenes act on, with every problem any of them shows, and no product or person. Drawn once, when the first B-roll starting picture needs it, and every B-roll starting picture is drawn from it, so the scenes match.
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
A scene's moving picture, with its line's audio: the same audio its transcript was heard in. A talking scene's is its starting picture animated to speak that audio, so it lasts exactly as long. A B-roll scene's is made by Boreal-H3 with no sound, from its starting picture, in the fewest whole seconds that cover the audio (at least 5, at most 15). It isn't cut to its line: the audio is laid over its start, and it is kept whole, silent after the line, so its motion plays out. A B-roll line whose audio is too long for any clip is shortened before a clip is paid for, or, after 3 shortenings, said to camera instead. Made only once the picture is made and the audio heard, for the line as it stands. A scene whose clip is made is finished; a new starting picture plans it again until a clip is made from that picture. Shown to the user in the chat as soon as it is made, with its scene's number.
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
A photo of the product this page sells, kept with the job; scenes are made from them. A model picks them off a screenshot of the page with every picture numbered, comparing them with the shop's own record of the product and its official photos, so photos of other products on the page ("you may also like", bundles, line-ups) are left out. Each is fetched at its biggest size, copies of one photo are kept once, gallery first, up to 50. When the page can't be screenshotted, the picker fails or the page blocked our reader (a block, error, login or "verify you're human" page), the photos the page declares for search engines are used instead, and a notice says so. When photos were declared ones or fewer than 2 were kept, the user is shown them and the ad isn't planned until they answer: go ahead, or attach their own.
_Avoid_: Image, picture (for these), asset

**Page panel**:
The panel in the chat where the shop owner pastes the product page's text and attaches its photos, shown when the page can't be read: it can't be fetched, or its text is a loading screen, cookie wall, bot check or error page. It stays open until the app has what it asked for, or a page is read from a new link. The normal chat stays open under it.
_Avoid_: Shed, form, manual mode

**Owner-pasted page**:
Page text the shop owner pasted in the page panel. Code runs the same junk check on it as on a page, then keeps it as the page text, so the planner, the fact check and the line checks read it like a page that was read. The owner is the source of it.
_Avoid_: Override, manual input, pasted facts

**User-supplied fact**:
Something the user states that the page does not, such as a promo code or a replacement line. Used as written and not fact checked, because the user is the source.
_Avoid_: Override, manual input

### Fixing a scene

**Note**:
The shop owner's own words about a scene of the ad they were shown, such as "the tea should actually pour". Kept verbatim with the fix plan made from it, as the record of what they asked. Not a scene step's note, which is the instruction to the model making the step.
_Avoid_: Feedback, comment, complaint

**Fix**:
One scene's row of a fix plan the shop owner said yes to, as code runs it: the change in plain words, what it will cost, and which step is made again (the clip; the starting picture and then the clip; or the line, then its audio, transcript and clip), which the shop owner isn't told. A scene that already has a clip is made again only for a fix, after the yes: nothing is paid for before it. A new picture's or clip's change is passed word for word as the redo's note. A new line is a can't when it takes longer to say than the scene can last; once said yes to, the scene's audio, transcript and clip are made again by code with no note, from the starting picture it has, moving as its clip did: the picture changes only when the shop owner asks for something different on screen, and a new line asked for with such a change is a new starting picture carrying the new line, and the new line counts as the shop owner's own fact, so it isn't fact checked. A correction is planned again, and the new plan replaces the old. Nothing the shop owner didn't say yes to is ever made. When every scene of a plan has landed, the ad is assembled again by code.
_Avoid_: Change request, edit, revision

**Fix plan**:
What the decider plans to change in each scene from the shop owner's notes of one round, kept with those notes word for word: per scene a new starting picture, a new clip, a new line, going back to an earlier version, or no change, said in plain words, with its cost, any can't, and the shop owner's photos it uses; and at most one question for the shop owner. Shown to them in the chat and run by code once they say yes. A chat has at most one plan waiting for their answer: a newer one replaces it.
_Avoid_: Fix list, proposal

**System note**:
What code tells the producer, in a short sentence, of a fix plan it runs: the plan shown, the fixes started, a scene done and what it cost, a failed fix check with its two versions, an error, the new ad shown. Never a picture, frames, judge words, version lists or the decider's reasoning. The shop owner isn't shown it.
_Avoid_: Producer message, event

**Decider**:
The AI that reads the shop owner's messages once the ad has a clip, sorts each one (a note, yes or no to the plan waiting, or something else, which the producer answers), looks at the starting pictures, clip frames, versions and photos it needs, and answers with the fix plan. It never talks to the shop owner and makes nothing: code shows its plan and runs it. It can't decide a scene it hasn't looked at, or go back to a version it hasn't looked at.
_Avoid_: Fixer, planner

**Fix check**:
A judge asking a fix's complaint back about the new version it made, shown beside the old one: a new starting picture before its clip is paid for; a new clip by the clip judge itself, which on a fix's clip also sees the old clip and is asked one more question, whether the change is made, read once from the clip its remakes kept (its answer never fails the clip or remakes it); a new line is never checked. A pass carries on as before. A new starting picture that fails is first drawn again, told what the check found still wrong, at most twice, each redraw checked again against the version the shop owner complained about; only a fail with no redraw left, or any clip's fail, reaches the shop owner. A fail makes nothing more for the scene and assembles no ad: the chat shows the old and new versions and why, with anything the clip judge still sees, with the reason as the note the scene's card is filled with, and the scene waits for the shop owner's word: go ahead carries it on with the new version as it is, anything else is planned again. An answer that can't be had leaves it unchecked, and the fix carries on. Kept with its fix and both versions.
_Avoid_: Fix judge, verification, QA

### Machinery

**Tool**:
One thing an agent can do, such as reading the page or making a scene's clip. The agent chooses which to call and in what order; the tool itself enforces the rules that matter.
_Avoid_: Step, action, function, capability

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
Round 4's ads made again with every starting picture and clip taken from what round 4 saved, and their voice and music faked (evals/owner_notes), so shop owners' notes can be sent to them for the price of the text models. The decider's replay and rule cases (evals/decider) start from copies of them.
_Avoid_: Fixture ad, test ad

### Checks

**Planning check**:
A check that looks only at text, before anything is rendered. The fact check and the length fit. Always runs, including when quality checks are switched off.
_Avoid_: Validation, pre-flight, gate

**Fact check**:
Comparing every price, number, product name and claim in a line against the page text. A claim the page does not state counts as not matching. A line that has not passed cannot be rendered.
_Avoid_: Verification, accuracy check

**Quality check**:
A check on something already produced, such as a picture or a clip. On unless switched off as a whole, except the B-roll starting picture check, which always runs. Built so far: the B-roll starting picture check, which looks at the picture, beside the shop photo it was drawn from, for objects that can't exist (a part on the wrong side, two sides of one object seen at once) before a clip is paid for; and the B-roll clip check, which looks at the clip right after it is made for objects that can't exist and a turn that shows a side that isn't real (10 frames a second) and for movement or change no real camera could film (5 frames a second), and keeps its ruling on the clip.
_Avoid_: QA, review, gate

**Auto-fix**:
What happens when the B-roll clip check fails a scene's clip: the fixing agent takes the scene over at once. It sees the plan, the page, the shop photos, the place, the presenter and every try's frames with what the check said, decides what to change (the action, the result, the start picture, both prompts, the line), and films a new try; each try is checked too. It stops when the check passes a try, which is then the scene's clip, or when it judges the scene can't be filmed well, at most 6 tries in all. Every try is a version. When it gives up, a scene from the ad's first making is said to camera, and a fix's scene keeps the clip it had before; either way the shop owner is shown every try, each with what the check saw, the agent's pick marked and why it stopped, and can pick one with one tap, for nothing. The ad doesn't wait. The shop owner isn't asked anything first: no yes is needed for it. A first clip the check can't settle (too few answers) is kept as it is, and the shop owner is told it went unchecked.
_Avoid_: Retry, redo (a redo is made for a fix), round (a round is one batch of owner notes)

**Try**:
One new clip of a scene the fixing agent films after the clip check failed it, with or without a new start picture. Numbered from 1; the clip the check failed first is the first clip. Every try is a version.
_Avoid_: Round, remake, attempt

**Version**:
One starting picture and the clip made from it, as the shop owner was shown them, numbered from 1 in the order shown ("v2 black toilet"). A new picture starts one; a new clip of the same picture, each try, and anything brought back by going back is one of its own. The shop owner and the decider can go back to any version.
_Avoid_: Variant (a variant is another job), copy

**Reference photo**:
A photo the shop owner attaches to a note, showing how something in a scene should look, such as the gel. The decider names the thing to take from it, such as "the black toilet", on the plan the shop owner says yes to. A clean picture of only that thing is drawn from the photo, and it wins over the product photos wherever they disagree, in every scene whose starting picture shows it, for every later fix until the shop owner says otherwise. The decider adds other scenes that show it unless the shop owner says it is only for the scenes they named. A photo too unclear to copy the thing from, or one sent before the ad is planned that isn't of the product, isn't used: the shop owner is asked for another, or to send it with a note. A fix with a reference photo is checked against it: the fix check and the clip check's question about the shop owner's change are shown the photo and say first what it shows. Where the decider answers the thing is in the starting picture, the starting picture is drawn again with it; where more of it shows during the action, the clip is made in reference mode with it. The shop owner isn't told which.
_Avoid_: Reference picture, owner picture

**Reference mode**:
How every B-roll clip is made (Julian 2026-10-16): the video model gets the drawn starting picture as Image 1, told the clip starts like it, then the clean pictures of the reference photos that only show during the clip, each named with what it shows. A reference photo goes in as a picture of the thing alone, drawn from it; no raw photo is ever sent. One switch in code goes back to before, when a clip without such pictures was made from its starting picture as its exact first frame.
_Avoid_: Reference picture mode, way 3

**Owner part**:
A thing of the product no photo shows, drawn from the shop owner's words or invented by the planner, that the shop owner must approve in the starting picture before the scene's clip is made. The planner asks for a photo of it, one sentence describing it, or leave to draw it and show it first. The scene keeps what the part is, where its looks come from and one sentence of how it looks, and when the shop owner said yes. It is one thing for the whole ad: a yes covers the part as it stands, its looks and its photos, on every scene that shows it and has no clip yet, so later pictures and fixes of it need no new yes; a correction (new words or a new photo) changes it on every such scene and needs a new yes.
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
