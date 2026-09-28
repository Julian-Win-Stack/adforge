# What makes a good short video ad, and how AdForge applies it

On 2026-09-25 I researched what makes a short vertical video ad work, for the kind of ad AdForge makes: a person holding a product and talking to the camera. I trusted the sources in this order: the ad platforms' own studies and guidance (TikTok, Meta, Google/YouTube, Amazon), then measurement firms (Kantar, Ipsos, System1, Realeyes, DAIVID), then the platforms' ad policies and the FTC. Opinions from UGC agencies and ad-analytics tools are marked **practitioner** every time they appear, because they are not platform findings. This page lists each lesson, the evidence behind it, and exactly where AdForge applies it, or why it doesn't yet. Status was checked against the code on 2026-09-26.

**Bottom line: of the 7 lessons that are not about B-roll, AdForge fully applies 2 today (captions on every ad, and no text over the product), mostly applies 1 (nothing that obviously looks AI-made, to be checked again when quality checks exist) and partly applies 2. Two are not applied yet: keeping text out of the areas the apps cover with their buttons, and changing the picture every few seconds. The other 13 lessons are about B-roll and are being built in #66, which isn't merged yet.**

## Words used on this page

| Term | What it means | In AdForge |
|---|---|---|
| **UGC** (user-generated content) | Video that looks as if an ordinary person filmed it on their phone, not a studio advert. | What AdForge makes |
| **B-roll** | Footage where nobody talks to the camera: the product up close, hands using it, the place it's used. | A B-roll scene (#66): the line's audio plays over a clip that isn't the person talking |
| **Hook** | The first 1–3 seconds, which decide whether the viewer keeps watching or swipes away. | The first scene |
| **CTA** (call to action) | The part that tells the viewer what to do, such as "Shop now". | The end card, not built yet |
| **Lo-fi** | Looks cheaply made and filmed on a phone. The opposite of "polished". | The look every picture prompt asks for |
| **Safe zone** | The part of the vertical frame that the app's own buttons, labels and captions don't cover. | Where captions and overlays should sit |
| **Shot**, **cut** | A shot is one unbroken piece of footage. A cut is the switch to the next shot. | One scene is one shot |
| **CTR** | Click-through rate: the share of viewers who tap the ad. | — |

**How a lesson can be enforced**, from strongest to weakest:

- **By how the code is built:** there is no way for it to go wrong.
- **By a validator:** code rejects a bad answer from the model, and the model has to try again.
- **By a check:** a planning check blocks the ad until it's fixed.
- **By a prompt:** the model is asked to do it. Nothing checks that it did.

## Summary

| Lesson | Evidence | Status | Where in the code |
|---|---|---|---|
| End with the offer, said and written | TikTok: +87% conversion | **Partly applied** | `PLAN_INSTRUCTIONS` in `backend/jobs/planning.py` (prompt) |
| Captions on every ad | TikTok: +58% recall | **Applied** | `captions()` and `assemble` in `backend/jobs/assembly.py` (how the code is built) |
| No text over the product | TikTok Shop rule | **Applied** | `TEXT_STYLES` in `backend/jobs/assembly.py` (how the code is built), plus `STARTING_PICTURE_INSTRUCTIONS` in `backend/jobs/scenes.py` (prompt) |
| No text where the apps put their buttons | Meta: bottom 35%; Google: top 10%, bottom 25% | **Not applied yet**, #74 | `TEXT_STYLES` puts text too close to the edges |
| Nothing that obviously looks AI-made | TikTok: +25% CTR for live action; Kantar | **Mostly applied**, to check again with quality checks | Phone-video look in `STARTING_PICTURE_INSTRUCTIONS` and `CLIP_MOTION_PROMPT` (prompt) |
| The picture changes every 2–3 s, not faster than 1.5 s | Google, Amazon, Realeyes; practitioners | **Not applied yet**, #75 | — |
| A length that suits the platform | TikTok: 21–34 s; Meta: 6–15 s | **Partly applied** | `fits_target` in `backend/jobs/checks.py` (check), only when a target length is set |
| 13 lessons about B-roll | See below | **In progress in #66** | — |

## The lessons in detail

### End with the offer, said and written on screen

**What the sources found.** TikTok's machine-learning study of shop ads found that "a human voiceover illustrating the product, combined with a written offer" converted 87% better ([TikTok, 2021](https://ads.tiktok.com/business/en-US/blog/creative-that-drives-conversions); "thousands" of ads). Google asks for the call to action to be both heard and seen, with branding in the last 5 seconds ([Google ABCD playbook](https://www.thinkwithgoogle.com/_qs/documents/15987/ABCDs_PDFPlaybook_April2022_Final.pdf)), and an end card of about 2 seconds ([Google, bumper ads](https://www.thinkwithgoogle.com/_qs/documents/1664/youtube-bumper-ads-best-practices-video-editing.pdf)).

**For an ad made from one product page:** the offer is the price the page gives, said by the person and written on screen.

**How AdForge applies it: partly.**

- **Covered.** `PLAN_INSTRUCTIONS` (`backend/jobs/planning.py`) tells the planner that one line must say the price a buyer pays today, and lets it give that scene a price overlay. If the page gives no price, or several, the planner must ask the shop owner rather than guess. This is a **prompt**.
- **Not covered.** Nothing puts the price at the end, or asks for it there. There is no end card ("Shop now"): spec #1 names it and says it is built later, and #13 decides what it should say. The B-roll work chose not to add a rule for the last scene, because the best-measured ending (voice over the product, with the price written) is itself a B-roll scene.

### Captions on every ad

**What the sources found.** Captions gave +58% recall ([Lumen for TikTok, 2021](https://ads.tiktok.com/business/creativecenter/quicktok/online/Power_Creative_Elements/pc/en)). Over 75% of Reels plays have the sound on, but Meta still recommends captions ([Meta Reels guide](https://www.facebook.com/business/f/1126214425544596?file_name=Whats_New_On_Reels)). Google's scoring tool rewards on-screen text that matches the speech ([ABCD Detector](https://github.com/google-marketing-solutions/abcds-detector)).

**How AdForge applies it: applied, by how the code is built.** When the finished ad is assembled, `captions()` in `backend/jobs/assembly.py` builds captions from the words actually heard in each clip's audio, a few words at a time and timed to the speech. They are always drawn onto the video. There is no setting that turns them off. Because they come from the heard words, not the script, a voice mistake shows up in the captions instead of being hidden.

### No text over the product

**What the sources found.** TikTok Shop: "Do not overlay any text or stickers on the product" ([TikTok Shop](https://seller-us.tiktok.com/university/essay?knowledge_id=2816204956665642&lang=en)).

**How AdForge applies it: applied, in two parts of different strength.**

- **How the code is built (strong).** `TEXT_STYLES` in `backend/jobs/assembly.py` draws text in only two places: captions in a band along the bottom and overlays in a band along the top. Nothing is ever drawn in the middle of the frame. `MOST_OVERLAY_CHARACTERS` in `planning.py` is a **validator**: it rejects an overlay long enough to wrap onto a second line and reach down over the face.
- **A prompt (weaker).** `STARTING_PICTURE_INSTRUCTIONS` in `backend/jobs/scenes.py` asks for the product "held at chest height so the top and bottom of the frame stay clear". Nothing checks the picture that comes back. If the model puts the product low in the frame, a caption can cover it.

B-roll scenes (#66) will need the same rule in their own picture prompt.

### No text where the apps put their buttons

**What the sources found.** Each app covers part of a vertical ad with its own buttons, labels and captions:

| Platform | Keep text out of |
|---|---|
| Meta | The bottom 35% (40% with a disclaimer) ([Meta Reels guide](https://www.facebook.com/business/f/1126214425544596?file_name=Whats_New_On_Reels); [Meta help](https://www.facebook.com/business/help/980593475366490)) |
| Google (YouTube Shorts) | The top 10%, the bottom 25% and the right 10% ([Google](https://business.google.com/us/ad-solutions/youtube-ads/shorts-ads/)) |
| TikTok | Varies with the length of the ad's caption ([TikTok](https://ads.tiktok.com/help/article/tiktok-auction-in-feed-ads?lang=en)) |

**How AdForge applies it: not applied yet.** In `TEXT_STYLES` (`backend/jobs/assembly.py`), captions sit 120 px above the bottom of a 1920 px frame (about 6%), and overlays 100 px below the top (about 5%). Both are inside the covered areas. On Meta or YouTube, the app's own buttons would sit on top of AdForge's captions. This is #74. The fix is small (bigger margins), but the caption band then moves up towards the product, so it has to be decided together with where the starting picture holds the product.

### Nothing that obviously looks AI-made, especially people

**What the sources found.** TikTok measured live action at +25% CTR over computer-generated imagery ([TikTok, beauty ads, 2023](https://ads.tiktok.com/business/creativecenter/quicktok/online/tiktok_creative_accelerator/pc/en)). Kantar found that ads which obviously used generative AI branded worse on average, and that "AI-generated visuals, especially of people, can be jarring". Very realistic AI product pictures did fine ([Kantar, Nov 2025](https://www.kantar.com/north-america/inspiration/advertising-media/rethinking-ai-generated-advertising); "hundreds" of ads).

**How AdForge applies it: mostly, by prompt.** We'll look at this again once quality checks exist and we can see how real clips come out.

- **Covered.** Every starting picture is asked for "a natural, casual phone-video look" with no added text or logos (`STARTING_PICTURE_INSTRUCTIONS`), and every clip for a person who "talks to the camera naturally, like a casual phone video" with "minimal hand movement" (`CLIP_MOTION_PROMPT`, `backend/jobs/scenes.py`). The picture is made from the real product photo, so the product itself isn't invented.
- **Still open.** Nothing checks whether a finished clip looks artificial. That comes with the quality checks (#20, #47). The person is always AI-made, because that is what AdForge is (see "Where we chose differently").

### The picture changes every 2–3 seconds, but not faster than about 1.5 seconds

**What the sources found.**

- **Change often.** Google asks for more than two shots in the first five seconds ([Google ABCD reference guide](https://services.google.com/fh/files/misc/youtube_google_abcd_reference_guide_en.pdf)), and its scoring tool counts under 2 s per shot as good pacing ([ABCD Detector](https://github.com/google-marketing-solutions/abcds-detector)). TikTok and Meta say "fast scene changes" and short scenes, without numbers ([TikTok Creative Codes](https://ads.tiktok.com/business/library/Creative_Codes_One_Pager_CA.pdf); [Meta](https://www.facebook.com/business/help/304846896685564)). Practitioners say every 2–3 s ([Influee](https://influee.co/blog/raw-ugc-to-ads), practitioner).
- **But not too fast.** Amazon's moderation rejects "jumpy, jarring, overly fast-paced cuts" ([Amazon](https://advertising.amazon.com/library/guides/sponsored-brands-display-ads-moderation)). Realeyes found that "high frequency edits … reduced attention" ([Realeyes](https://adverteyes.ai/food-delivery-ads-attention-driven-creative-boosts-sales-by-13-5/)).

**How AdForge applies it: not applied yet.** In AdForge a scene lasts exactly as long as its spoken line, so pacing depends on how long the lines are. `PLAN_INSTRUCTIONS` only asks the planner to fit the whole script into the target length. It doesn't ask for short lines, and no validator sets a longest or shortest scene. A 30-second ad could be two 15-second scenes. This is #75. #66 plans short lines for B-roll scenes, but not for talking scenes.

### A length that suits the platform

**What the sources found.** The platforms disagree:

- **TikTok:** 21–34 s gave +280% conversion ([TikTok, 2021](https://ads.tiktok.com/business/en-US/blog/creative-that-drives-conversions)). Ads must run 5–60 s ([TikTok policy](https://ads.tiktok.com/help/article/tiktok-ads-policy-ad-format-and-functionality)).
- **Meta:** 6–15 s ([Instagram help](https://www.facebook.com/business/help/188534925073536)).
- **Google:** under 60 s for Shorts, and 10–30 s for ads meant to drive action ([Google](https://support.google.com/google-ads/answer/16041697?hl=en)).
- **Amazon:** 30 s beat 15 s by 12% (65,000 ads) ([Amazon Ads](https://advertising.amazon.com/library/guides/one-video-full-funnel-success)).

**How AdForge applies it: partly.** The shop owner can set a target length. The planner is asked to fit it (prompt), and then `fits_target` in `backend/jobs/checks.py` measures the script with the voice's real speaking speed. It sends the script back to be shortened if it runs more than `LENGTH_ALLOWANCE_SECONDS` (1 s) over (a **check**). If the shop owner sets no target, nothing limits the length, and AdForge doesn't know which platform the ad is for. A default length per platform is #76.

## Lessons about B-roll: in progress in #66

These are being built in #66 (B-roll scenes, and switching every clip to Boreal). It isn't merged yet, so none of them is applied today. Each will be documented here with its place in the code once #66 is merged.

**The shape of the ad**

- **Several different scenes beat one person talking the whole time.** Shop ads with a "variety of scenes" converted 38% better than "one person selling a product in a continuous shot" ([TikTok, 2021](https://ads.tiktok.com/business/en-US/blog/creative-that-drives-conversions)). Today every AdForge ad is one person talking.
- **Open on the person talking to camera, with the product in view.** Speaking to camera: +50% "hooking power" ([MetrixLab for TikTok, 2023](https://ads.tiktok.com/business/creativecenter/quicktok/online/Spark-Ads-Creative-Playbook/pc/en)). A person on screen: +17% retention, most in the first second ([Realeyes, 2021](https://adverteyes.ai/resource/the-guide-to-the-tiktok-creator-economy/); only 36 videos). Product within 5 s: +8% ([Amazon Ads](https://advertising.amazon.com/library/guides/one-video-full-funnel-success)).
- **Don't open on a product shot alone.** System1 linked product shots in the opening seconds to lower brand memory ([System1](https://system1group.com/the-creator-effectiveness-playbook)). DAIVID found that leading with the product "too early" cut 25%-view rates by 44% (5,000 creator videos) ([DAIVID, 2026](https://blog.daivid.co/2026/06/04/new-billion-dollar-boy-research-powered-by-daivid-reveals-how-creator-instinct-drives-brand-impact-on-social/)).
- **B-roll belongs in the middle:** how the product is used, what it does, the proof.
- **The person isn't on screen the whole time.** Looking at the camera for under half the ad went with 1.7× CTR ([VidMob, 1,400+ TikTok ads](https://vidblog.vidmob.com/blog/12-creative-insights-for-better-performing-tiktok-ads), practitioner data).

**What B-roll shows**

- **The product being used.** +175% conversion rate ([TikTok, 2023](https://ads.tiktok.com/business/creativecenter/quicktok/online/tiktok_creative_accelerator/pc/en)). A realistic setting: +16% (65,000 ads) ([Amazon Ads](https://advertising.amazon.com/library/guides/one-video-full-funnel-success)). How-to, recipe and unboxing ads changed behaviour 1.5× more than plain reviews (289 TikTok ads) ([Ipsos, 2025](https://www.ipsos.com/sites/default/files/ct/publication/documents/2025-07/IpsosViews_ShortFormSocialMisfits_POV_.pdf)).
- **What the voice says, at the moment it says it** ([Influee](https://influee.co/ie/blog/effectively-use-your-raw-review-videos-and-b-roll-shots), practitioner). TikTok Shop asks for the spoken description to be paired with the close-up.
- **Proof, not mood.** Meta: don't "focus only on lifestyle or vibes" ([Meta Reels guide](https://www.facebook.com/business/f/1126214425544596?file_name=Whats_New_On_Reels)).

**Sound**

- **The person's voice keeps running over B-roll.** Voiceover with a written offer: +87% ([TikTok](https://ads.tiktok.com/business/en-US/blog/creative-that-drives-conversions)). Music or voiceover: up to +13% conversions over 10,123 lift studies ([Meta, 2025](https://www.facebook.com/business/news/the-science-of-the-hook-how-to-supercharge-your-reels-performance)). Voiceover: up to +20% ([Google](https://www.thinkwithgoogle.com/_qs/documents/18344/Google_UKI___Creative_in_Performance_Max_Playbook.pdf)), +10% ([Amazon Ads](https://advertising.amazon.com/library/guides/one-video-full-funnel-success)).
- **The brand name is said while the person is on screen.** Google found brand mentions from people on screen beat a plain voiceover ([Google](https://business.google.com/aunz/think/marketing-strategies/creating-youtube-ads-that-break-through-in-a-skippable-world/)).

**Look**

- **Filmed-on-a-phone look, not studio polish.** Lo-fi: +33% consideration ([Lumen for TikTok, 2021](https://ads.tiktok.com/business/library/Global_SMB_Creative_Playbook.pdf)).
- **One look across the ad.** Meta: don't mix footage in very different styles ([Meta Reels guide](https://www.facebook.com/business/f/1126214425544596?file_name=Whats_New_On_Reels)).

**Honesty**

- **A picture is a claim, just like a sentence.** The FTC judges an ad by its words, phrases and pictures together. TikTok Shop allows AI-made demos only if they show the product's real size, material, functions and normal results. AdForge's rule that facts come only from the page and the shop owner (ADR 0002) is being extended to what B-roll shows.
- **No before/after pictures of bodies or skin.** Restricted or banned by TikTok, Meta, Google, Snap and Pinterest ([TikTok Shop](https://seller-us.tiktok.com/university/essay?knowledge_id=4545471832983342)).

## Where we chose differently, and why

| Lesson | What AdForge does instead | Why |
|---|---|---|
| Avoid obviously AI-made people (Kantar) | Every ad's person is AI-made | That is the product: an ad from a link, with no filming. We reduce the risk with the phone-video look and minimal movement, and #66 adds B-roll, which puts less of the ad on the person. |
| Remind the shop owner to label AI content | No reminder | AdForge isn't used by real shop owners to post ads, so there's nothing to label yet. |
| The offer at the end | No rule about the last scene | The best-measured ending (voice over the product, with the price written) is a B-roll scene. A fixed "last scene is the person" rule would block it. |

## What no source measured

These are judgement calls. They will be adjusted once real ads have been made and watched.

- **How much of the ad is B-roll.** No source gives a share. #66 gives the planner a table per product type (about 50% for food, 0% for apps by default), as a guide it may go against if it says why. The numbers sit between the practitioners' cap of 30–40% and VidMob's "on camera less than half the time".
- **The shortest scene, about 1.5 s.** The sources warn against cutting too fast, but none gives a number. Every scene also costs a model call, which pushes the same way.
- **At most two B-roll scenes in a row.** Dropped: it was our own guess, with no source behind it.
- **When a talking-head-only ad is enough.** No source gives a rule. The signals are whether the selling point can be seen on camera, and whether the product or the person is the star.

## Gaps

Lessons not applied yet or only partly. Per the repo's rule, a fix is written into the spec (#1) first, then into the tickets that implement it.

| Gap | Issue |
|---|---|
| Captions and overlays sit inside Meta's and Google's covered areas | #74 |
| Nothing keeps scenes short, so the picture may not change every few seconds | #75 |
| No default length when the shop owner sets none, and no idea which platform the ad is for | #76 |
| The price isn't placed at the end, and there's no end card | #13 decides the end card; no issue for placement |
| Nothing checks that the product stays out of the text bands, or that a clip doesn't look artificial | Quality checks, #20 and #47 |
| All B-roll lessons | #66 |
