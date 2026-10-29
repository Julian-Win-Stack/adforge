# Page videos are watched whole by the watcher, not read as thinned frames

Supersedes [0007](0007-videos-read-with-the-planners-own-prompt.md).

The video API needs the real movement of the product, cut from the page's own video at the
right seconds (spec #210). The video reader of ADR 0007 saw at most 80 thinned frames in
labelled grids and a separate transcript, and named a few seconds worth a picture; it gave no
ranges and no product colour. In the watcher test (`runs/watcher-test/results.md`, Oct 27
2026) Gemini Flash watching the whole video with its sound gave better moments than GPT shown
the app's grids. So every page video with a file is now watched once, as the page is read, by
one call (`jobs/watcher.py`, purpose `watch_video`, gemini-3.8-flash through the gateway's
`watch_video`): the video cut to 480 px on its short side, with sound, one frame a second.
The watcher writes:

- whether the video shows the product the page sells (requirement 23): one that doesn't is
  thrown out with a grey notice;
- its moments, numbered per video in time order, each one action of at most the video API's
  15 s (the watcher is told the limit; a longer moment is refused by the answer's schema and
  asked again once);
- one look frame per product state, with the picture's fixed fields: pulled with ffmpeg from
  the file and added to the library with its video and second;
- the product's colour in the video, in one word;
- the transcript: this replaces the separate speech-to-text call.

The thinning to 80 frames (`jobs/video_frames.py`), the labelled grids, the reader
(`jobs/video_reader.py`) and the `transcribe_video` call are gone. ffmpeg stays for pulling
frames and cutting pieces. Every video is watched at the same time, a video over 3 minutes is
not watched (the owner is told which), and there is no byte cap: length is the only cut
(requirement 23, Julian Oct 27 2026).

## Consequences

- One Gemini call per page video, paid once: a page read again watches only videos not yet
  watched. Gemini spend is Julian's own; at $0.75/$3.75 per M tokens a 30 s video is about a
  cent.
- Until the planner reads the library and the moments (spec #210 point 9), it gets what it
  got before: each video's transcript and its look frames as video pictures.
- A video that can't be watched (an error, two refused answers) is told in red and the ad is
  planned without it.
- A safety-filter refusal (requirement 22, #222) is asked again as it is, then without the
  sound (kept with no transcript); then the video is halved in time at multiples of 5 s, each
  half probed with a one-word call (`probe_video`, the watcher's model at its lowest thinking
  level), refused halves over 5 s halved again (at their middle when no 5 s mark fits), a
  refused part of 5 s or less dropped, at most 16 probes a video (the parts not probed by then
  are dropped). A look frame past its part's end is dropped unless the part ends the video. Each part kept is watched, told which seconds it is, and merged in the video's
  own seconds. Every refusal, even one fully saved, is told in red (reason: sound, pictures or
  all) and kept on the watch (`VideoWatch.refused`). A video refused all over is thrown out, so
  a page read again doesn't search it again. Worst case: 3 watches, 16 probes and a watch per
  part kept. A timeout or a rate limit is never a refusal.
