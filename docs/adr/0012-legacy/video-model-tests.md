# Video model tests

Results of the throwaway tests from #2, run on 2026-09-17. Five talking-photo video models were given the same starting picture and the same 5.70-second voice line. Each one animates the person in the picture saying the line, so the voice is ours and the clip should last as long as the audio.

**Pick for v1: HeyGen Avatar IV**, because it was the cheapest. Every model's output was good enough; what this project has to prove is that the system works without errors, not that the video looks best.

## Results

| Model | Where it runs | Cost of the test clip | Per second of video | Time to make | Clip length (audio 5.70 s) | Size |
|---|---|---|---|---|---|---|
| **HeyGen Avatar IV** | HeyGen API | **$0.20** | ~$0.035, measured* | 52 s | 5.72 s | 1080×1920 |
| P-Video-Avatar, 1080p | Replicate, `prunaai/p-video-avatar` | ~$0.23–0.27** | $0.045 | 55 s | 6.04 s | 1088×1920 |
| Kling AI Avatar v2 Standard | fal, `fal-ai/kling-video/ai-avatar/v2/standard` | $0.40 | $0.056 | 4.5 min | 7.2 s | 720×1280 |
| Kling AI Avatar v2 Pro | fal, `fal-ai/kling-video/ai-avatar/v2/pro` | $0.83 | $0.115 | 5.6 min | 7.2 s | 1072×1920 |
| OmniHuman 1.5 | fal, `fal-ai/bytedance/omnihuman/v1.5` | $0.93 | $0.16 | 3.3 min | 5.8 s | 1088×1920 |

\* HeyGen does not publish a clear per-second price. The wallet went from $5.00 to $4.80 for this clip (12 quota units).
\** Our estimate from the clip length was $0.27. Replicate's own record billed 5 seconds, which is about $0.23.

**Video cost of a 30-second ad** (5 scenes of about 6 seconds): HeyGen ~$1.05, P-Video ~$1.35, Kling Standard ~$2.10, Kling Pro ~$4.30, OmniHuman ~$4.80. The Kling numbers include the padding below.

## What we learned

- **Every model kept our voice audio unchanged.** The pauses in each clip line up with the voice file to within 0.02 s. So the transcript and word timings can be made from the voice audio before the clip is paid for.
- **Clip length follows the audio, except for Kling.** Both Kling models returned 7.2 s of video for 5.7 s of voice: about 1.5 s of extra video after the voice ends. fal billed all 7.2 s, about 26% extra. The extra can be cut off for free, but it is still paid for. One clip is not enough to tell whether Kling always adds 1.5 s.
- **Same person:** the frame check said the person matched the portrait in all five clips.
- **Small label print goes soft in every model.** The brand name "LUMA" stayed readable in every clip. The smaller lines ("Vitamin C Serum 15%", "30 ml") only read correctly on the still starting picture.
- **The vision check makes up small print instead of saying it can't read it.** Each frame was checked by `gpt-5.6-luna` and `gpt-5.6-terra`, and both marked every clip as failed because of blurry small print. Asked to copy the label text, they gave:

  | Clip | gpt-5.6-luna read | gpt-5.6-terra read |
  |---|---|---|
  | Starting picture (still) | Vitamin C Serum 15% ✅ | Vitamin C Serum 15% ✅ |
  | HeyGen | Vitamin C Serum 15% ✅ | could not read |
  | P-Video | "Retinol & Squalane 5%" ❌ made up | "Hyaluronic C Serum, 15%" ❌ made up |
  | Kling Standard | "Vitamin C Serum 10%" ❌ | Vitamin C Serum 15% ✅ |
  | Kling Pro | Vitamin C Serum 15% ✅ | Vitamin C Serum 15% ✅ |
  | OmniHuman | could not read | could not read |

  A frame check costs about $0.001 with luna and $0.01 with terra, and takes 5–12 s.

## The test clips

These files are not in git (about 50 MB together). They are on the machine that ran the tests, next to this repo, in `../adforge-spike/out/`:

| Model | File |
|---|---|
| HeyGen Avatar IV | `../adforge-spike/out/clip_heygen.mp4` |
| P-Video-Avatar | `../adforge-spike/out/clip_pvideo.mp4` |
| Kling AI Avatar v2 Standard | `../adforge-spike/out/clip_kling_std.mp4` |
| Kling AI Avatar v2 Pro | `../adforge-spike/out/clip_kling_pro.mp4` |
| OmniHuman 1.5 | `../adforge-spike/out/clip_omnihuman.mp4` |
| Two scenes stitched with ffmpeg: text, captions, music | `../adforge-spike/out/ad_stitched.mp4` |
| The starting picture every model animated | `../adforge-spike/out/scene.png` |
| The voice line every model was given | `../adforge-spike/out/line1_trimmed.mp3` |

## Other parts tested in the same run

| Part | Service and model | Result | Cost |
|---|---|---|---|
| Portrait | OpenAI `gpt-image-2.5-sunburst`, 720×1280, quality high | Good | $0.03 |
| Starting picture: portrait + product photo | OpenAI `gpt-image-2.5-sunburst` edit, 1152×2048, quality high | Same face, sharp label | $0.06 |
| Voice | Inworld `inworld-tts-2`, voice designed from a text description | Same voice across lines | $0, free plan |
| Transcript and word timings | ElevenLabs `scribe_v2` | Matched the script word for word | $0, free plan |
| Music | fal `sonilo/v1.1/text-to-music` | 15.06 s for 15 s asked, no singing, voice still clear over it | $0.04 |
| Stitching | ffmpeg, the full Homebrew build `ffmpeg-full` | 2 scenes with text, captions and music in 5.4 s. The plain `ffmpeg` build can't draw text | $0 |

Not tested: ElevenLabs music needs a paid plan, so Sonilo was tested instead. P-Video-Avatar at 720p (about half the 1080p price).

**Total spent on all tests: $2.90.** fal $2.20, Replicate $0.27, OpenAI $0.23, HeyGen $0.20.
