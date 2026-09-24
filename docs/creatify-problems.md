# Problems I saw in Creatify's agent, and how we handle them

Creatify has an agent that makes UGC video ads (their public report: https://creatify.ai/research/agent). I ran it 5 times against their live product. This page lists the problems I saw in those runs, taken from the problem statement in the spec (#1), and what this project does about each one.

> **These are my own observations, not verified findings.** They come from my 5 runs and their transcripts, not from Creatify's report, and Creatify has not confirmed any of them. Five runs is a small sample, and where I give a cause for a problem, it is my reading of what I saw. If you work at Creatify and something here is wrong, open an issue and I'll correct it.

**Status** means:

- **Tested**: the fix was tried for real in the throwaway tests (#2, results in [video-model-tests.md](video-model-tests.md)).
- **Designed**: the fix is written into the spec and a ticket, but not built yet.
- **No fix yet**: we know about the problem but have no fix for it yet.

## Summary

| # | Problem | Status |
|---|---|---|
| 1 | Clips and ads came out too long | Tested (clip length), Designed (planning) |
| 2 | Silent gaps between scenes | Tested |
| 3 | Wrong facts in the script | Designed |
| 4 | Captions hid voice mistakes | Tested (transcript), Designed (order), no ticket yet (check) |
| 5 | A failed quality check was used anyway | Designed |
| 6 | Nothing compared scenes with each other | **No fix yet** |
| 7 | The same scene was produced twice | Designed |
| 8 | Bad handoffs between parts | Built for the page check, Designed for the rest |
| 9 | Work lost on resume | Designed |
| 10 | Hard to fix anything | Designed |
| 11 | Small label text is unreadable in clips (found in our tests) | **No fix yet** |
| 12 | The vision check makes up text it can't read (found in our tests) | **No fix yet** |

## Problems I saw in Creatify's runs

### 1. Clips and ads came out too long

**What I saw.** All 5 runs missed the requested 15 seconds, landing between about 16 and 32 seconds. As far as I could tell, three things caused it:

- The video model stretched each clip to fit the spoken line: 4 seconds requested came back as 5 to 10.
- The pacing check assumed 3 words per second, but the real voice spoke 1.6 to 2.5.
- One plan was already 20 seconds long before anything was made.

**How we handle it.**

- **Clip length is set by the voice audio.** We make the voice audio first, then a talking-photo model animates the person saying exactly that audio. **Tested:** HeyGen returned 5.72 s of video for 5.70 s of audio. The Kling models add about 1.5 s at the end, which is one reason we don't use them.
- **No fixed words-per-second number.** The voice's real speaking speed is measured when the voice is created, and every length decision uses it. **Designed** (#5).
- **The length is checked before money is spent.** If the user sets a target length, the whole script's length is worked out from the measured speed. If it doesn't fit, the system stops and asks the user. **Designed** (#5).

**What is still open.** Scenes are not redone for length. If the final ad still misses the target, the gap is recorded and shown, not fixed.

### 2. Silent gaps between scenes

**What I saw.** Each clip was trimmed by a fixed amount instead of using the word timings, leaving dead air between scenes.

**How we handle it.** Every voice line is transcribed with the time each word starts and ends. Clips are cut exactly where the speech starts and ends. **Tested:** two scenes were stitched this way with ffmpeg with no gap. **Designed** for the app (#7).

### 3. Wrong facts in the script

**What I saw.** The wrong price appeared in 2 of the 3 ads that showed a price. The same prompt gave two different prices in two runs. Nothing compared the script with the product page.

**How we handle it.** The page text is stored with the job. Before anything is made, a fact check compares every price, number, product name and claim in the script with that text. A claim that doesn't match goes back to be rewritten. If the page itself is unclear, for example it shows two prices, the user is asked. **Designed** (#4, #5).

### 4. Captions hid voice mistakes

**What I saw.** Captions were forced to match the script. When the voice said a wrong or repeated word, the captions still showed the script, so the mistake shipped unnoticed.

One Graza run shows how, as far as I could tell from its transcript:

- The voice came out of the video model together with the clip. The director split the audio out of the finished clip, transcribed it, and scored the transcript against the script.
- Scene 2's line said "Graza Frizzle". The transcript heard "Graza's Frizzo", and the scene scored 0.833 against the script. The director put it down to the voice plus the sizzle sound in the clip.
- The clip was already paid for, so catching the mistake could no longer save money. The director passed the scene and told the producer a voice redo was optional.
- The captions were corrected to match the script. The producer called all three scenes clean "on the first pass", assembled the ad without redoing the voice, and quoted the line from the script, not what was said, in its final summary.

So the voice was checked, but the check changed nothing: it ran after the money was spent, a failing score only produced a note, and the captions covered the mistake.

**How we handle it.**

- Captions come from a transcript of what the voice actually said, not from the script. **Tested:** ElevenLabs Scribe wrote down the voice line word for word, with times. **Designed** for the app (#7).
- The transcript is made from the voice audio **before** the clip is paid for, because every video model we tested keeps our audio unchanged. Comparing the transcript with the script then catches a voice mistake while it costs a fraction of a cent to redo, instead of a whole clip. **Designed** (#19).
- The transcriber hears only the voice, with no sizzle or music behind it, so a mismatch is more likely to be a real voice mistake than a mishearing. Not tested.
- The comparison itself is a quality check, built after the first run. It has no ticket yet.

**Why this might be better, and why we're not sure yet.**

- **Nothing is built yet.** Until the comparison runs on real lines, this is a plan, not a result.
- **It depends on the video model.** It works only while the video model keeps our audio unchanged. HeyGen did in our tests. A model that makes its own voice, as in the Graza run, loses the advantage.
- **Comparing words is harder than it looks.** The script says "600°F" and the transcript says "six hundred degrees". A brand name can be written down wrong even when it was said right. A plain word-for-word comparison would fail good audio, so numbers have to be written the same way on both sides and small spelling differences allowed.
- **The transcriber can mishear too.** A mismatch can mean the voice was wrong or the transcript was wrong, and the check can't always tell which. What happens when a redo still doesn't match is not settled.

**Not the same as Creatify's "audio scoring".** Their report has a section called "Audio scoring", but it means music made to fit the cuts, the way a film is scored: "a music track is generated to the cut cadence". It doesn't describe checking the voice against the script. Music timed to the cuts is on the spec's list for after v1 (#1).

### 5. A failed quality check was used anyway

**What I saw.** An image failed its check but was used anyway. It looked like a rule that said not to retry was the reason.

**How we handle it.** A failed quality check never ships. There is no path where failed content reaches the user: the scene is retried, and if it still fails it is marked failed and the final video is not assembled. **Designed** (#1, #8). The quality checks themselves are built after the first run.

### 6. Nothing compared scenes with each other

**What I saw.** The same person looked different from scene to scene. Only a human watching noticed.

**We don't have a fix for this yet.** A scene comparison check (same person, same clothes, same setting) is planned, but it is built after the first run, once we see how often this really happens. What we know so far: in our tests, the frame check correctly said the person in each clip matched the portrait. We have not yet compared two scenes with each other.

### 7. The same scene was produced twice

**What I saw.** Two workers produced the same scene. It looked like a worker got stuck, and another worker took over the scene without stopping the first one.

**How we handle it.** A scene can only be worked on by one worker at a time. A stuck worker is stopped before another one takes the scene over. A test starts two workers on the same scene and checks that only one produces it. **Designed** (#8).

### 8. Bad handoffs between parts

**What I saw.** Data passed between parts of the agent arrived broken: the colour palette in the wrong format in all 3 runs for one brand, a scene length as a decimal, and voice IDs missing from a brief, only noticed after work had started.

**How we handle it.** Every handoff has a fixed shape (a Pydantic model), and it is checked in code before every model call, so a broken handoff stops before any money is spent. **Built** for the one model call that exists so far, the page check (#3). Every new model call goes through the same place.

### 9. Work lost on resume

**What I saw.** Runs paused mid-job and lost their files, so work that was already paid for had to be made again.

**How we handle it.** Progress is saved to the database as it happens, and files are kept through one small piece of storage code. After a crash the system knows where every scene stopped, continues from there, and never makes or pays for anything twice. Cancelling never deletes anything. **Designed** (#8).

### 10. Hard to fix anything

**What I saw.** To fix a problem, the user had to describe in words what was wrong and in which scene.

**How we handle it.** The user pauses the finished video and comments at the moment that is wrong. The system knows where each scene starts and ends, so it knows which scene the comment is about, and redoes only the part that was wrong. **Designed** (#9).

## Problems found in our own tests

### 11. Small label text is unreadable in clips

In every video model we tested, the brand name stayed readable but the smaller print on the product ("Vitamin C Serum 15%", "30 ml") went soft once the person moved. On the still starting picture it was sharp.

**We don't have a fix for this yet.** Making the clips themselves look better is the video model's job and out of scope. The open question is what the brand check should require of a clip: the brand name only, or every line of the label.

### 12. The vision check makes up text it can't read

When asked to copy the blurry label text from a clip frame, both OpenAI vision models sometimes wrote words that are not on the product, for example "Retinol & Squalane 5%" and "Hyaluronic C Serum" for a vitamin C serum. The checks still failed those frames, but only because what they read didn't match.

**We don't have a fix for this yet.** Reading small print is only reliable on the still starting picture. This has to be settled when the brand check is built.
