# How it works, part by part

Code map for coding agents. The [README](../README.md) is the overview.

This is the details behind the diagram in the [README](../README.md), part by part, with the code for each. The README's [words I use](../README.md#words-i-use) explain the in-house names. In the code the main agent is called the **producer**: it talks with the user and runs the ad.

The producer's tools are in `backend/agents/producer.py` (`PRODUCER`); the scene agent's tools are in `backend/agents/scene_agent/__init__.py` (`TOOLS`). What the parts do:

- **Reading the page.** Keeps only this product's text and photos. Page videos are watched whole on Gemini and cut into one-action moments for the ad's library. A page that can't be read can be pasted in the page panel. ([why](why/read-the-page.md))
- **Planning.** The planner uses the main-looking size or pack and its price, the clear photos it has, invented parts and ordinary use when needed, then tells the user its choices without a question (`backend/jobs/planning.py`). The plan is 3 or 4 scenes by default: the first talking, the middle ones B-roll, at least one B-roll scene per ad. Each talking scene has its own style (selfie, propped, talking while using it) and product state (in its box, out of it, in use, beside its box), so no two in a row look the same (`backend/jobs/plan_rules.py`, [ADR 13](adr/0013-more-broll-whole-clips.md)). One movement per B-roll clip. ([why](why/video-model.md#one-movement-per-clip))
- **No questions, on purpose.** Decided and built on Nov 15, 2026 ([#662](https://github.com/Julian-Win-Stack/adforge/issues/662)): the agent doesn't ask the user for photos, videos or permission. It picks the size that looks like the main one and goes with its price, draws a part no photo shows, films the product's ordinary use, tries a different shot by itself when a scene keeps failing during first ad-making, and says in one line what it chose. The user can ask for changes afterwards with a note. Only a page that can't be read, or no product photo at all, still stops it. Why: AdForge isn't used by real users yet, and a question stops a test run (one regression ad stopped after $18.98, waiting for a photo nobody would send). And as a user, I don't want to be asked those questions. I want it to figure things out by itself.
- **Planning checks.** Every line and what each scene shows is checked against the page, the user and the photos, then against how long the clip can last. A failing line is rewritten up to twice; if it still fails, the producer fixes it with its scene tools (`producer.py:RunPlanningChecks`). Every fact comes from the page or the user ([ADR 2](adr/0002-facts-come-only-from-the-page-and-the-user.md)).
- **Scene agents.** The producer starts one agent per scene with an instruction (`start_scene_agent`). The agent picks the photos, draws a starting picture, films it with Boreal-H3 on Creatify, reads the checks and tries again. H3 speaks the checked line in the clip, in the presenter's voice words; no voice file is made. Only the producer talks to the user.
- **Checks, run by code.** Every starting and end picture is checked before filming (`scene_agent/picture_check.py`). The clip judge checks whether the product's shape or colour changed or an extra piece appeared, and whether something passed through a body or a solid object; a fault fails the clip and tells its remake the fault and seconds (`jobs/clip_judge.py:judge_clip`). Every clip, talking and B-roll, gets the clip judge inside the agent's `film` tool (`scene_agent/film.py:Film.run`). The agent can't skip the checks or overrule a verdict. A pass puts the clip in the ad. ([why](why/clip-judge.md))
- **Caps.** Per run: 6 starting pictures, 6 end pictures and 6 clips, with a few more after the scene changes (`scene_agent/lifecycle.py:MOST_TRIES`). While making the first ad, the producer tries one different idea after a give-up, once per scene per user message. After that the ad keeps the last clip, or tries the same line to camera if no clip exists. A talking scene that still makes no clip is removed so assembly can continue. Every try stays available on its card. [Asked changes](#asked-changes) have their own count alongside these per-run caps.
- **Look.** The producer can't see. It asks one question about a picture, clip or version, and a sub-call answers in words. It decides nothing (`backend/agents/look.py`).
- **User's notes.** The producer passes a note to the scene agent as its own instruction plus the user's exact words, which code checks against the user's messages (`backend/agents/owner_quotes.py`). An accepted note can open a new run while the agent is working. A photo sent with a note reaches that scene's agent (`backend/agents/note_photos.py`). On-screen words are changed with an overlay, for free, without filming again.
- **Presenter.** Made up from the page and the user's wishes, or drawn from a photo the user sends in the chat (`producer.py:RemakePerson`). Changing her voice films every scene again, so the producer quotes the cost and waits for a yes.
- **Versions.** Every try is kept. The producer or the user can put any version back. A version whose voice or presenter changed since quotes its refilm cost first (`backend/agents/version_refilms.py`).
- **Make a copy.** A chat with nothing being made can be copied whole, rows and history included, so notes can be tried on the copy while the original stays as it was. Files are shared, not duplicated (`backend/chat/session_copy.py`).
- **Money and control.** No yes button before paid work, except a voice change or a refilm, which wait for the user's yes to a quoted cost. The producer acts, then says what it cost. The pause button cancels steps not started yet; running ones finish, and a scene agent parks and later picks up where it stopped (`backend/agents/pause.py`).
- **What the app can't do.** The producer is told the app's limits (`docs/limitations.md`) and the video model's hard limits (`docs/h3-limits.md`), so it says "can't, because" and offers what can be done before any paid call.
- **Hard list.** Code refuses another product's page mid-ad, a page's how-to photo sent to the picture model, and the video API's limits (`backend/agents/hard_list.py`). A drawn part is checked and filmed without waiting for the user's approval.
- **Notices.** Every fallback or failure posts a notice written by code, not the producer. The user sees only what they must act on; the rest goes to the developer record and Sentry (`backend/jobs/notices.py`). When an AI safety filter blocks a step the ad can't do without, the ad stops and says why.
- **Shared limits.** Calls to the picture model, Firecrawl and transcription wait in one line across workers, under each account's limits (`backend/gateway/service_limits.py`).
- **Assembly.** The clips play whole with their own sound, cut half a second after their last word (`backend/jobs/assembly.py`). Code draws captions from each clip's transcript, each scene's overlay and music under the voice, then the end card: product photo, name, one line from the page, price and website. The music plays at a fifth of its volume while someone talks and rises to near full (0.9) wherever no one does for half a second or more: between lines and on the end card. It dips over 0.2 s just before the voice and swells back over 0.5 s after it (`assembly.py:speech_times`, `music_level`), timed from the words heard in each clip, script or not; a scene with sound but no timed words keeps the music down all through it.

**Agentic in code, not yet in practice.** The producer's instructions give a usual order for a first ad, and tools refuse steps out of order, so a first ad nearly always runs the same way. The freedom shows mostly on the user's notes.

**How the checks look.** The clip judge's product check is told to describe the product in its reference photos, then in each frame, before its verdict. A product fault (its shape or colour changed, or an extra piece appeared) or something passing through a body or a solid object fails a clip, for first clips and note remakes alike (`backend/jobs/clip_judge.py`). Every clip is transcribed for timing; captions always show the scene's script line. The picture check answers 4 pass-or-fail questions for a starting picture and 2 for an end picture, naming every wrong part.

## Asked changes

An **asked change** is one result the shop owner wants. The producer files a scene note with the
number of an existing asked change or a sentence describing a new result. To link several scenes to
one new change, it uses the same sentence for each scene in one call. Each scene has its own try
count, even if it moves to a different position in the ad.
[`PassNote.run`](../backend/agents/scene_tools.py) · [`file_note`](../backend/agents/asked_changes.py)

- **One try per scene per message.** Combine changes to the same scene in one note. A failed try
  keeps the previous clip. A technical breakdown with no clip leaves the try available in the
  same message. Filming another try after a counted run needs another owner request.
  [`counted`](../backend/agents/asked_changes.py) · [`StartSceneAgent.run`](../backend/agents/scene_tools.py)
- **Before a repeat films.** The first repeat filing with tries left returns the earlier
  instructions, results and costs. The producer sends a clearly different action, or resends the
  note as it is if the action already differs. If a try failed and tries remain, the producer
  explains what's still wrong, what it tried and the cost.
  [`_brief_repeat` and `run_ended`](../backend/agents/asked_changes.py)
- **After three tries by default.** The result of the last try tells the producer the change
  is closed on that scene. If the owner still wants it, the producer says plainly that it
  couldn't make the change, explains what it tried and offers the closest alternative.
  A different result the owner accepts gets its own tries.
  [`ASKED_CHANGE_TRY_LIMIT`](../backend/adforge/settings.py) · [`_closing_guidance`](../backend/agents/asked_changes.py)
- **When one change spans scenes.** A repeat can remake only the scene the owner asks about.
  A scene added later starts at its first try. If one scene closes and the change is still
  wanted, the producer explains which scenes have it, then waits for the owner to choose a
  different shot for that scene, a different approach for all linked scenes, or a return to
  earlier versions. Either filmed choice starts a new asked change. `use_version` restores
  earlier clips that are available for free.
  [`_linked_choices`](../backend/agents/asked_changes.py) · [`UseVersion.run`](../backend/agents/producer.py)
