# Where our agent falls short of Creatify's

- Date: 2026-11-02. Our side is read in the code on main `b3c0f58` (paths under `backend/`).
- Creatify's side comes from three sessions of Creatify's ad agent run on 2026-11-01 (a
  garlic press, a hair brush and a floss): every tool call, every picture it looked at, and
  its own answers to questions.
- All grades are Julian's. He is the only one who grades the videos.

> **How much to trust it.** Three Creatify sessions is a small sample, and Creatify hasn't
> confirmed any of it. Its skill and playbook text is private, so this page knows what its
> agent did, not the instructions behind it. The other direction, the problems seen in
> Creatify's agent, is in [creatify-problems.md](creatify-problems.md).

## The two agents

**Creatify's:** one agent does everything. It reads the page, picks photos, draws each start
frame, looks at it, writes the video prompt in the same words, renders the clip, checks it and
builds the edit. Its tools are plain: a picture model, a video model (Boreal H3), music, a
transcriber and the edit. It looks at every picture it uses.

**Ours:** a producer agent with about 30 tools runs the ad (`agents/producer.py`). The plan is
one model call (`jobs/work.py: plan`). Separate model calls write each clip's prompt. A clip
judge checks each B-roll clip (`jobs/clip_judge.py`), and a fixing agent takes over a scene
when its clip fails (`agents/fixing_agent.py`).

## Summary

| # | Where ours falls short | In one line |
|---|---|---|
| 1 | The prompt chain | Up to 4 writers shape one clip's words. |
| 2 | Hard to trace, hard to verify | A bad clip has many possible culprits, and checking a fix costs paid clips. |
| 3 | Writers that can't see | The prompt writer and the producer never see the pictures. |
| 4 | Context cut between agents | The fixing agent doesn't get the chat or the producer's reasoning. |
| 5 | Checks only after paying for video | B-roll has no still to check first. |
| 6 | Planning ahead needs eyes | The plan is made once, before any picture, by a call that can't see. |
| 7 | No answer to "fix or let go" | The judge is stricter than Julian, and fails nobody can fix still get up to 6 paid tries. |
| 8 | Speed | About 15 to 20 minutes an ad, against Creatify's 7 to 10. |

Then, for fairness: [what ours does that Creatify's doesn't](#9-what-ours-does-that-creatifys-doesnt),
[Creatify's holes may be trade-offs](#10-creatifys-holes-may-be-trade-offs), and
[who should write the prompts](#11-who-should-write-the-prompts-the-producer-or-one-helper).

## 1. The prompt chain

The words the video model gets for one B-roll clip pass through up to 4 writers:

1. **The planner** (`jobs/work.py: plan`) writes the scene's line and what it shows (`shows`,
   `usage`, `result`), and picks its pictures.
2. **The first-prompt writer** (`jobs/first_send.py: write_first_send_prompt`) writes one label
   per picture and one sentence.
3. **Code** adds the speed line and an "Image N is ..." line per picture (`jobs/first_send.py`,
   `jobs/cut_and_send.py: labelled_prompt`).
4. **Creatify's prompt enhancement** rewrites the prompt inside Creatify's API, because we send
   it as `auto` (`adforge/settings.py: CREATIFY_PROMPT_ENHANCEMENT`,
   `gateway/creatify_adapter.py`).

A remake adds its own writer. After a failed clip, the fixing agent writes the new prompt.
After a shop owner's note, the producer's instruction goes to a rewriter
(`jobs/work.py: _rewrite_motion_prompt`). Both then pass through steps 3 and 4.

The pictures have a chain too. Page photos are picked (`jobs/work.py: pick_photos`), labelled
(`jobs/library_items.py: label_photo`), picked again by the planner, then cut out, recoloured
or face-swapped (`jobs/cut_and_send.py`).

**Creatify's:** one writer. The same agent writes the frame prompt, looks at the frame, then
writes the video prompt with the same words for the person, room, light and product.
Enhancement is off.

## 2. Hard to trace, hard to verify

Julian's words: "if we wanna change the final output, it is quite hard to find the culprit and
to really change it. And verify whether it changed or not."

- **Any link can be the cause.** A wrong clip can come from the plan, the picture picks, the
  prompt writer, the code's labels or the enhancement.
- **The final prompt is hidden.** Our code never reads back what the enhancement wrote, so
  nobody sees the words the video model actually got.
- **A change gets reworded.** A fix to one writer passes through the writers after it, so it
  may not reach the video model as written.
- **Checking costs money.** The only way to see whether a change worked is to pay for new clips.

**Creatify's:** one place to look. The prompt the agent wrote is the prompt the video model
got, and a fix goes straight into its next prompt.

## 3. Writers that can't see

- **The first-prompt writer never sees the pictures it labels.** It gets each picture's
  number, label and colour, and the planner's words (`jobs/first_send.py: LabelledPicture`; its
  `call_model` passes no `images`).
- **The producer can't see.** Its instructions say "You can't see pictures or videos
  yourself" (`agents/producer.py`). Its `look` tool (`agents/look.py`) shows a picture to
  another model, which answers in words.
- **Nobody sees how a clip opens before paying for it.** B-roll has no start frame, so the
  first look at a clip is the paid clip.

The one agent of ours that does see is the fixing agent: it's shown each try's frames
(`look_at_try`) and the library (`look_at_library`).

**Creatify's:** the agent looks at every photo and every frame it uses before it writes
about them.

## 4. Context cut between agents

The fixing agent starts with the plan for every scene, the page, what failed, what the clip
was sent, the library and the planner's note on how the product behaves
(`agents/fixing_agent.py: _intro`). It doesn't get the chat with the shop owner, or the
producer's own reasoning. For a shop owner's note it gets the note, one line saying what the
producer asks, and what the producer's look found.

Why it was built that way: to keep the producer's context small.

**Creatify's:** one agent keeps everything in one context. That has its own risk, a long
context, but the three sessions don't show whether it hurt.

## 5. Checks only after paying for video

- A B-roll clip never gets a start picture (`jobs/work.py: make_starting_picture` asserts
  it), so the clip judge is the first check, and it runs on the paid clip.
- Cut-outs and face swaps are checked before sending. Recolours and the drawn room aren't
  (`jobs/recolour.py`, `jobs/work.py: _draw_once`).
- The talking scene's start picture goes to HeyGen unchecked. The start-picture check
  (`jobs/work.py: judge_starting_picture`) is only called from an eval.

**Creatify's:** every shot that shows the product starts from a frame the agent drew and
looked at first. Its words: "Any shot showing the product starts from a frame I've approved."

## 6. Planning ahead needs eyes

Some scenes need thinking ahead about what the camera will see. For a pre-workout, a clear
shaker shows the powder's colour, so the frame must show the right contents, or the clip can
start from the drink already mixed.

- **The plan is made once,** by one call, before any picture exists (`jobs/work.py: plan`).
  The producer's `plan_ad` refuses to plan again ("The ad was already planned").
- **The producer can rewrite one scene** (`edit_scene`), but it can't see a picture, so it
  only hears about one in words (point 3).
- **The only agent that sees and can change a scene's plan is the fixing agent.** It can
  change the action, the pictures and the line, then film again. But it only starts after a
  clip fails, so a scene that passes but shows the wrong idea is never rethought.

**Creatify's:** the same agent plans, looks at every frame and changes its own plan before any
check runs. Here our design can't be as natural as one agent with eyes, however hard we try.

## 7. No answer to "fix or let go"

Julian's words: "we just simply don't know when to let go and when to fix."

**Ours fixes until it passes.** A failed clip gets up to 6 tries
(`agents/fixing_agent.py: MOST_TRIES`). If none passes, the last try stays in the ad anyway, and
the shop owner is shown every try (`jobs/work.py: say_the_agent_gave_up`).

- **The judge is stricter than Julian.**
  - Round 6 Glow Recipe scenes 2 to 4: the judge failed every try, and Julian passed all
    three: "the judge is correct but it has been too over sensitive ... the product actually
    works like that."
  - Round 7 Beautyblender scene 3 took 5 tries; Julian said none should fail.
  - Gemini Flash gave the same verdicts as our GPT judge on those clips, so the problem is the
    bar, not the model.
- **Some fails nobody can fix.** The floss failed on motion on every try, ours and Creatify's.
  Julian: our agent "can catch the mistake, but it cannot fix it because it's just a
  limitation of the video generation API."
- **Retries do sometimes work.** They rescued 4 of 9 of Julian's real first-clip fails.

**Creatify's lets almost everything go.** Its rule is at most 2 redos per shot and a time
limit, then it ships the best take with the flaws named. It shipped known flaws in all three
sessions, and twice ran its own ship gate again after it failed. Julian failed those flawed
clips.

So neither agent knows when to fix and when to let go. This is open.

## 8. Speed

- **Creatify's:** about 7 to 10 minutes from the first answer to the shipped ad (session
  timestamps: 7, 9 and 10 minutes).
- **Ours:** about 15 to 20 minutes and about $5 an ad. This comes from project records, not a
  fresh measurement.
- Part of the gap is point 7: every fix of ours is a new paid clip, while Creatify trims a
  clip down to its clean seconds instead.

## 9. What ours does that Creatify's doesn't

None of these is proven to give better ads. They are what ours has, with the hole in each.

- **A fact check.** Every line, and what each scene shows, is checked against the product
  page before anything is made (`jobs/checks.py`, the producer's `run_planning_checks`).
  Creatify's script had wrong facts in Julian's earlier runs
  ([creatify-problems.md](creatify-problems.md), problem 3). The hole: our fact check counts
  as not measured.
- **A judge on every B-roll clip.** It catches flaws Creatify shipped, such as the floss
  changing shape. The hole: when no try passes, the last try still goes into the ad
  (point 7), so the flaw ships anyway, only later and at a higher cost.
- **The page video.** In hand round 2 the garlic press scene passed 4 of 4 with the page video
  sent to the video model. Creatify's agent chose not to send it to the video model, because the video showed an older red model, and its garlic press ad failed: "it didn't
  use the video." The hole: that's one scene in one hand round.
- **Recolouring.** We recolour photos to the colour being sold (`jobs/recolour.py`). Creatify
  did none in the three sessions. The hole: recolours aren't checked before sending (point 5).

Of all our graded clips, 46 passed, 59 failed and 22 were unsure. Creatify's agent was
tried on 3 products ours never got right, and Julian judged its first version good on 2 of
them. Its garlic press ad failed, and the other two failed only once it was asked to fix
their first version.

## 10. Creatify's holes may be trade-offs

| Hole | Chosen on purpose? | Evidence |
|---|---|---|
| Didn't send the page video to the video model in any of the three sessions | Yes | It can send one, and chose not to. In Garject it used the video only to describe the motion and pick moves to avoid, because the video showed an older red model "that would have pulled red parts into the black one". Its general reason: reference mode "re-imagines the product", so any shot with the product starts from an approved frame. |
| Avoids hard actions | Yes | It keeps a list of weak spots (floss on teeth, lids, hinges) and shows "the moment right before it", "the product's feature" or "the feeling after it" instead. |
| Ships known flaws | The rule is chosen | At most 2 redos and a time limit, then the best take ships with its flaws named. It also shipped past its own failed gate. |
| Its frame check never asks "can this happen?" | No sign | It's missing from its own check list, and it never names it as a choice. |

Each trade-off buys speed or safety, and each costs something Julian graded. Without the page
video, its garlic press failed. Avoiding hard actions left its first versions with "little
demonstration."

## 11. Who should write the prompts: the producer, or one helper?

Open. This lays out both ways for Julian's call.

**A. The producer writes the frame and video prompts itself, as Creatify's agent does.**

- For: one writer, so the plan, the frame and the video prompt use the same words.
- For: a fix goes to the source, and there's one place to look for the culprit.
- For: it deletes `write_first_send_prompt`, `_rewrite_motion_prompt`, and likely the fixing
  agent's prompt writing.
- Against: the producer can't see pictures today, so it would write the video prompt blind to
  the frame unless it's given eyes. Creatify's agent looks at every frame.
- Against: its context and the cost of each call grow (about 30 tools, plus photos and frames).
- Against: the biggest code change (the producer's prompt and tools, the fixing agent's role,
  and the tests that pin today's prompt path).

**B. One helper call replaces `write_first_send_prompt`.**

- For: a smaller change, and the tested chain stays.
- For: it can be shown the frame as a picture.
- Against: it's still a chain (planner, helper, code, enhancement), so finding the culprit is
  still two or more steps.
- Against: the producer's intent reaches it only as fields (`shows`, `usage`, `result`), which
  loses detail.

The open fact is whether the producer can be given eyes cheaply.
