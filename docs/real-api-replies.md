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

Not yet run through `backend/gateway/heygen_adapter.py`. These are the fields the spike (`../adforge-spike/step3b_heygen.py`) read from real replies in September 2026, and all the adapter relies on. `backend/tests/test_providers.py` replays them.

- **Upload** a picture or audio: `POST /v3/assets`, multipart `file`. The asset's ID is `data.asset_id`. The spike uploaded an MP3; the adapter uploads the line's WAV.
- **Ask for a clip**: `POST /v3/videos` with `type: "image"`, the picture's and audio's asset IDs, a `motion_prompt`, `aspect_ratio: "9:16"` and `resolution: "1080p"`. The clip's ID is `data.video_id`.
- **Ask how it's getting on**: `GET /v3/videos/{video_id}`. `data.status` is `completed` or `failed` once done, and something else until then. A made clip is fetched from `data.video_url`, which only works for a while. Which field says why a clip `failed` hasn't been seen: the adapter reads `data.failure_message`, then `data.error`.
