# AdForge

A chat app that turns a product page link into a finished short UGC video ad. The user talks to the producer in one box; it reads the page, plans the ad, creates a presenter, makes each scene and assembles the video, asking questions when something is missing rather than guessing.

## Language

### The conversation

**Session**:
One chat thread, named and returned to. Holds the whole conversation and every ad made in it.
_Avoid_: Chat, thread, conversation, project

**Message**:
One turn in a session, from the user or the producer. The only way the user and AdForge communicate.
_Avoid_: Comment, note, activity entry

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

**Scene step**:
One piece of a scene's work, run in the background: its starting picture, and later its audio, transcript and clip. A scene tool starts it and returns at once; when it finishes or fails, the producer is told on its next turn and tells the user.
_Avoid_: Task, job, render

**Starting picture**:
A scene's first frame: the person holding the product, made from the portrait and one product photo. The clip is made from it.
_Avoid_: Start frame, keyframe, thumbnail

**Person**:
The presenter in an ad: one portrait and one matching voice. Every scene in a job shows the same person.
_Avoid_: Avatar, actor, character, persona

**Page text**:
The words a visitor sees on the product page plus the structured product data the page declares for search engines. The only place facts about the product may come from. Models read this, never the HTML.
_Avoid_: Page content, scraped text, HTML

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

**Overlay**:
Text drawn over a scene, such as the price.
_Avoid_: Caption, graphic, text layer

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
A closing scene carrying the call to action, such as "Shop now".
_Avoid_: CTA, outro, closing frame
