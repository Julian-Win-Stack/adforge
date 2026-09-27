# Real API replies

What the real portrait and voice services sent back to our requests, on 2026-09-19. Until this run, the code for #5 had only met fakes. The request bodies were the same ones `backend/gateway/inworld_adapter.py` and `backend/gateway/openai_adapter.py` send. `backend/tests/test_providers.py` replays these shapes, so a test fails if the code stops matching them. Long base64 values are cut short here.

## Inworld (`inworld-tts-2`)

**Design a voice**: `POST /voices/v1/voices:design`. The voice's ID is `previewVoices[0].voiceId`. Neither `voicePreviews` nor `previewId` exists.

```json
{
  "langCode": "EN_US",
  "previewVoices": [
    {
      "voiceId": "starry-sparrow-8090__design-voice-ec5cf8c1",
      "previewText": "Meet the Stoneware Mug from Kiln & Co. Yours for $24.00.",
      "previewAudio": "<WAV file, base64>"
    }
  ]
}
```

**Publish it**: `POST /voices/v1/voices/{voiceId}:publish`. The ID is `voiceId` at the top level, not inside a `voice` object, and it's the same ID the design gave.

```json
{
  "name": "workspaces/starry-sparrow-8090/voices/design-voice-ec5cf8c1",
  "langCode": "EN_US",
  "displayName": "AdForge test run",
  "description": "",
  "tags": [],
  "voiceId": "starry-sparrow-8090__design-voice-ec5cf8c1",
  "source": "TVD",
  "ageGroup": "",
  "gender": "",
  "categories": [],
  "promptLanguages": ["en-US"],
  "viewerState": null,
  "owned": false,
  "sharing": null,
  "languageCode": "en-US",
  "localizations": [],
  "workspaceTags": []
}
```

**Speak**: `POST /tts/v1/voice` with `LINEAR16` at 48,000 Hz. `audioContent` is a whole WAV file (it starts with `RIFF`): mono, 16-bit, 48 kHz. Inworld counted 47 characters for the 47-character line, so billing by `len(text)` matches.

```json
{
  "audioContent": "<WAV file, base64>",
  "usage": {"processedCharactersCount": 47, "modelId": "inworld-tts-2"}
}
```

**Silence in the audio.** The line "Hand-thrown, holds 350 ml, and dishwasher safe." (7 words) came back 5.40 s long. The first 0.08 s and the last 0.65 s were silent. The design's preview was the same: 5.52 s long, with 0.60 s silent at the end.

## OpenAI (`gpt-image-2.5-sunburst`)

**Draw**: `images.generate` with `size="720x1280"` and `quality="high"`. It sent back one 720 × 1280 PNG as `data[0].b64_json`. The usage was 42 text tokens in and 947 picture tokens out, which is $0.029 at our price list's rates.

```json
{
  "created": 1789849905,
  "background": "opaque",
  "data": [{"b64_json": "<PNG file, base64>", "revised_prompt": null, "url": null, "generation_id": "da89718d-fcc3-48b0-9259-af104a0c36e4"}],
  "output_format": "png",
  "quality": "high",
  "size": "720x1280",
  "usage": {
    "input_tokens": 42,
    "input_tokens_details": {"image_tokens": 0, "text_tokens": 42},
    "output_tokens": 947,
    "output_tokens_details": {"image_tokens": 947, "text_tokens": 0},
    "total_tokens": 989
  }
}
```

## HeyGen (Avatar IV)

Run for real through `backend/gateway/heygen_adapter.py` on 2026-09-24: one 5.70-second line, as a 48 kHz mono 16-bit WAV, with the spike's starting picture. HeyGen took the WAV, and the clip was made in 41 seconds (its `created_at` to `completed_at`): 1080×1920, 5.72 seconds, H.264 video with AAC audio. These are the fields the adapter relies on. `backend/tests/test_providers.py` replays them.

- **Upload** a picture or audio: `POST /v3/assets`, multipart `file`. The asset's ID is `data.asset_id`. The spike uploaded an MP3; the adapter uploads the line's WAV, which HeyGen takes.
- **Ask for a clip**: `POST /v3/videos` with `type: "image"`, the picture's and audio's asset IDs, a `motion_prompt`, `aspect_ratio: "9:16"` and `resolution: "1080p"`. The clip's ID is `data.video_id`.
- **Ask how it's getting on**: `GET /v3/videos/{video_id}`. `data.status` is `completed` or `failed` once done, and something else until then (`processing` was seen). A made clip's reply also gives `data.duration` in seconds. A made clip is fetched from `data.video_url`, which only works for a while. Which field says why a clip `failed` hasn't been seen: the adapter reads `data.failure_message`, then `data.error`.
- **A clip HeyGen doesn't know of** (asked on 2026-09-25 with made-up IDs): `404` with `error.code` `video_not_found` and `error.message` `Video <id> not found`. The adapter counts it as a clip that couldn't be made. Any other `404` isn't taken to be about the clip.

## fal (Sonilo v1.1)

Run for real through `backend/gateway/fal_adapter.py` on 2026-09-25: 5 seconds of music asked for, in the prompt `music_prompt("light upbeat lo-fi")` builds. fal answered in 11 seconds. The file was 190 KB, AAC-LC, 44.1 kHz stereo, in an MP4 file (`ftypiso5`), lasting 5.06 seconds.

- **Make music**: `POST https://fal.run/sonilo/v1.1/text-to-music` with `Authorization: Key <FAL_KEY>` and `{"prompt", "duration", "num_samples": 1}`. `duration` is whole seconds, at most 600. It answers once the music is made. The music is fetched from `audio.url`, without the key. The shape is from fal's model page; only `audio.url` is read.
- **Billed** at $0.0025 a second of music, as fal's model page said on 2026-09-17. The adapter bills the seconds asked for, not the few hundredths more that come back. Whether fal bills its own retries is still unknown.

## fal (Boreal)

**Audio sent inside the request is refused.** Sent as a `data:audio/wav;base64,…` data URI, every clip with a voice line failed when collected, with `Unsupported audio format: .bin. Supported formats: .wav, .mp3, .aiff, .aif, .aac, .ogg, .flac, .m4a` at `body.audio_url`. fal names a file sent inside a request from its content type, and doesn't know `audio/wav`, so it names it `.bin`. The picture, sent as `data:image/png`, was taken. So the line's audio is put in fal's storage first and sent as a link.

**fal's storage**, asked on 2026-09-27 through `backend/gateway/boreal_adapter.py`:

- `POST https://rest.fal.ai/storage/upload/initiate?storage_type=gcs`, the fallback fal's own Python client uses, answers `400` `{"detail":"Invalid storage type"}`. It no longer works.
- **A token to store files with**: `POST https://rest.fal.ai/storage/auth/token?storage_type=fal-cdn-v3` with `Authorization: Key <FAL_KEY>` and `{}`. It lasts 30 days.

  ```json
  {
    "token": "<cut>",
    "created_at": "2026-09-27T21:47:25.394105+00:00",
    "expires_at": "2026-10-27T21:47:25.394105+00:00",
    "base_url": "https://v3b.fal.media",
    "token_type": "Bearer"
  }
  ```

- **Store a file**: `POST {base_url}/files/upload` with `Authorization: {token_type} {token}`, `Content-Type: audio/wav`, `X-Fal-File-Name: line.wav` and the file as the body. The link keeps the name, and fetching it gives the same file back as `audio/wav`, without any key.

  ```json
  {"access_url": "https://v3b.fal.media/files/b/0aac27e0/hzumoERxCN0OZTqbpMb2L_line.wav", "uploaded": true}
  ```

**A talking clip from a stored line**, run for real the same day: a 3.6-second line (a 48 kHz mono 16-bit WAV) and a drawn 720×1280 face, `duration: 3.6`. fal said `COMPLETED` after about 73 seconds. The clip was 720×1280, 3.71 seconds, with the line's audio in it.
