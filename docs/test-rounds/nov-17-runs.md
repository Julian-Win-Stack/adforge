# Nov 17: two test runs (same 15 products again, plus 15 new ones)

| Run | Ads that worked | Final clips that worked | Cost |
|---|---|---|---|
| Same 15 products again | 9 of 15 | 51 of 58 | $163.50 |
| 15 new products | 12 of 15 | 54 of 58 | $109.79 |

**How we counted:** whole ads were graded by Julian's tap (his choice, Nov 18). A note on an ad or clip he passed stays a note, not a fail. Anything he didn't tap is a pass. If any complaint counts as a fail ([the usual way](README.md#how-we-grade)), the new 15 would be 8 of 15. Quality was not graded.

## What we tried
- 4 untested changes since Nov 14: fewer needless questions to the user, better selling lines, the whole product in the starting picture (a still image the video model starts from), and a simpler clip checker (an AI that watches each clip and passes or fails it).
- The same 15 test products, all at once on the Azure machine. $45.66 of the cost was lost when a paid service ran out of credit and the run restarted.
- 15 products never tested before (Allbirds, Fellow Stagg, Glossier, Touchland, Magic Spoon, Peak Design, Baggu, Wild One, Bala, Material Kitchen, Vuori, Tower 28, Stanley, Truff, Ruggable).
- A new AI grader ($89.43) tried on ads it was never tuned on.

On the same 14 ads as last time, 8 of 14 worked (before: 11 of 14). Ridge, Owala, Crocs and Brooklinen got worse; Bissell got better. Each ad was made once, so one flip can be chance.

## What broke, and what we did
Status words: [the summary](README.md#status-words).

| Problem | Why it happened | What we changed | Status |
|---|---|---|---|
| Hands or bodies break physics: Ridge, Owala, Crocs, Brooklinen, Material Kitchen, Stanley, Allbirds (7 of 11 failed clips) | Not found yet | Fix work started Nov 18 | Not fixed |
| Owala: a remake repeats the same failed action, all 3 attempts failed | Not found yet | Fix work started Nov 18 | Not fixed |
| Stanley: logo comes and goes, wrong colour and place | Not found yet | Fix work started Nov 18 | Not fixed |
| Furbo, Gorilla Glue: things appear or vanish ("The water disappear suddenly in the end") | Not found yet | Fix work started Nov 18 | Not fixed |
| Stanley: starting picture failed, no reason given | Not known | None | Not fixed |

Passed, kept as notes: Peak Design ad says $159.95, page says $99. The top label covers faces. Touchland and Tower 28 show a spray the page never shows.

## The new AI grader
| | Same 15 | New 15 | Both |
|---|---|---|---|
| Failed clips it caught | 2 of 7 | 1 of 4 | 3 of 11 |
| Its flags Julian agreed with | 2 of 6 | 1 of 9 | 3 of 15 |

- Its own bar is 8 of 10 caught and 7 of 10 flags right. It is far below both.
- It missed every hand failure except Stanley's, and the Stanley logo.

## What we learned
- Broken hands and bodies are now the main failure: 7 of 11 failed clips.
- The AI grader isn't ready to stand in for Julian.
- Nine failed ads have no written reason yet, so these runs can't be used as the new reference until they do.
