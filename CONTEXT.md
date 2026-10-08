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
A message the code posts, not the producer, when a step fell back to a worse source or failed. It says what failed, what was used instead, and what that means for the ad. Shown red when it affects the ad, grey when it is only worth knowing. Kept with the job as one of its warnings, and never given to a model.
_Avoid_: Alert, error message, system message

**Attachment**:
A file a message carries: a photo the user attached, or a picture, sound or video the producer made. Kept through the file store and shown in the chat.
_Avoid_: Media, upload, asset

**Brief**:
What the user asks for in their own words: the link, the length, a promo code, anything else. Not a form and not a stored record of its own.
_Avoid_: Request, prompt, order

**Interrupt**:
A message that arrives while the producer is working. The producer decides what it means for work already in flight.
_Avoid_: Cancel, abort, pause

### The agents

**Agent**:
A model in a loop that chooses which tool to call next. The producer is the only one built; the critic comes later.
_Avoid_: Bot, assistant

**Producer**:
The agent the user talks to. Reads the page, asks, plans, creates the person and the music, makes each scene and assembles the ad. The only agent that speaks in the chat.
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
A scene that shows the product rather than the person talking, such as a pan sizzling, while the person's voice says the line over it. What it shows is written in plain words in the plan, with its kind, who is in it, its usage, its result and the photos it needs (its B-roll details). What it shows, its usage and its result are fact checked like the line, and a scene that fails is rewritten whole, B-roll details included. Its clip is made by Boreal-H3 (on Creatify), one of two ways, picked by plain code: way 1 when the plan lists nothing it needs beyond its main photo, from a starting picture made from that photo; way 3 when the plan lists a need, from real shop photos sent as example pictures, each named in the clip's prompt ("Image 1 is the bottle; Image 2 is only the gel's colour"). Made with the same tools as a talking scene either way. The user is never told which scenes are which, except when a B-roll scene has to become a talking scene: a notice says so, with the reason.
_Avoid_: Cutaway, product shot, insert

**Scene step**:
One piece of a scene's work, run in the background: its starting picture, its line's audio, the audio's transcript, and its clip. A scene tool starts it and returns at once; when it finishes or fails, the producer is told on its next turn and tells the user.
_Avoid_: Task, job, render

**Starting picture**:
A scene's first frame, made from the portrait and one product photo: the person holding the product, or, for a B-roll scene made way 1, the "before" of what the scene shows, made from its main photo, with the portrait only when the presenter's face is shown. The clip is made from it. A B-roll scene that needs something its main photo can't show (way 3) has none: its picture step picks example pictures instead (the main photo, a photo for each need, then the portrait if its face is shown, at most 5), and its clip is made from those.
_Avoid_: Start frame, keyframe, thumbnail

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
A scene's moving picture, with its line's audio: the same audio its transcript was heard in. A talking scene's is its starting picture animated to speak that audio, so it lasts exactly as long. A B-roll scene's is made by Boreal-H3 with no sound, from its starting picture (way 1) or its example pictures (way 3), in the fewest whole seconds that cover the audio (at least 5, at most 15). It isn't cut to its line: the audio is laid over its start, and it is kept whole, silent after the line, so its motion plays out. A B-roll line whose audio is too long for any clip is shortened before a clip is paid for, or, after 3 shortenings, said to camera instead. Made only once the picture is made and the audio heard, for the line as it stands. A scene whose clip is made is finished. Not shown to the user on its own.
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
A photo of the product this page sells, kept with the job; scenes are made from them. A model picks them off a screenshot of the page with every picture numbered, comparing them with the shop's own record of the product and its official photos, so photos of other products on the page ("you may also like", bundles, line-ups) are left out. Each is fetched at its biggest size, copies of one photo are kept once, gallery first, up to 50. When the page can't be screenshotted or the picker fails, the photos the page declares for search engines are used instead, and a notice says so. The user can attach their own.
_Avoid_: Image, picture (for these), asset

**User-supplied fact**:
Something the user states that the page does not, such as a promo code or a replacement line. Used as written and not fact checked, because the user is the source.
_Avoid_: Override, manual input

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

### Checks

**Planning check**:
A check that looks only at text, before anything is rendered. The fact check and the length fit. Always runs, including when quality checks are switched off.
_Avoid_: Validation, pre-flight, gate

**Fact check**:
Comparing every price, number, product name and claim in a line against the page text. A claim the page does not state counts as not matching. A line that has not passed cannot be rendered.
_Avoid_: Verification, accuracy check

**Quality check**:
A check on something already produced, such as a picture or a clip. Can be switched off as a whole; the first full run happens with them off.
_Avoid_: QA, review, gate

### Not yet built

Named so the words mean one thing when these are built. Nothing implements them today.

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
