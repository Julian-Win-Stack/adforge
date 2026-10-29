# Product videos are read by a separate call with the planner's own prompt

Status: superseded by [0009](0009-videos-watched-whole-by-the-watcher.md) (spec #210, Oct 2026).

Shops often put the product opened, its inside, its before and after or the product in use only in
their gallery video, never in a photo, so the planner could not plan those scenes (round 5: a blush
whose applicator shows only in its video got no B-roll). Each product video with a file is now
thinned to at most 80 frames and read once, as the page is read, by its own call: the planner's model
with the planner's instructions word for word, the same page text, photos and conversation, the
video's transcript and the frames in labelled grids, plus one added job: "You ask the shop
owner nothing. Before you plan, name the video moments you would need as pictures, and why." The moments it keeps are cut from the video and
saved as product photos (video pictures) with its one line; the planner gets those and every
transcript, not the reader's description. (Julian, 2026-10-19 22:38; the job's wording
2026-10-20 02:23, after the first line, "You are not planning the ad here ... Think through the plan
you would make", kept nothing on the blush's video.)

Why a separate call and not the plan call: one call can look at about 80 small pictures, but the plan
call already carries up to 50 photos (the provider's limit) and runs again on every owner answer, so
the video's frames would crowd out photos and be paid for on each re-plan. The reader is paid once per
video. Why the planner's prompt verbatim: a reader with its own prompt would judge the video by its
own idea of a good moment and keep moments the planner doesn't need; thinking through the same plan
makes it keep what that plan would use.

## Consequences

- About 15 cents per gallery video on the page read, plus about 1 cent for its transcript; a
  re-plan never reads the video again.
- A video picture is a product photo like every other: looks only, never a reference photo, and the
  planner's 50-photo cap fills with page photos first, then video pictures, then the owner's.
- A video that can't be heard or read is left out with a line to the producer; the owner sees nothing
  and the ad is planned as before.
- Not read: videos only in the page body (YouTube refuses server downloads), files over 50 MB, the
  owner's own videos.
