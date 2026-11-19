# Round 1: First 5 full ads from just a product link (October 8, 2026)

**Result: 0 of 5 ads worked.** Product shots that worked: 8 of 13. Talking shots that worked: 12 of 17.

How we counted: Julian graded in chat, not by taps. Any complaint counts as a fail. By his own verdict 1 of 5 was OK (the handbag). See [how we grade](README.md#how-we-grade).

## What we tried
- 5 new products, made side by side: a Steve Madden handbag, a Smartish phone case, Lemi Shine shower cleaner, L'ange Le Duo (a hair tool that curls and straightens), Molly's Suds toilet cleaner.
- Talking shots (an AI presenter talking to camera) came from a presenter video service. Product shots (the product in use, nobody talking to camera) came from the video model.
- Julian watched each whole ad, then re-graded every clip the next day.

## What broke, and what we did
| Problem | Why it happened | What we changed | Status |
|---|---|---|---|
| All 5 ads: the voice cuts out in the last seconds | The sound mix stopped when the voice track ended, losing about 1.2 s | Pad the voice, trim sound to the picture length, test the last word is loud ([#116](https://github.com/Julian-Win-Stack/adforge/issues/116)) | Fixed (proven later) |
| All 5 ads: too many talking shots in the middle, not enough product shots | The planning step was told most scenes are a person talking | First scene talking, every middle scene a product shot | Came back |
| Toilet cleaner: camera zooms into the dirty toilet for no reason | When nothing happened, the plan moved the camera instead | Only a hand or the product moves. Later Julian said a camera move alone is fine | Dropped |
| Toilet cleaner: presenter claims the nozzle reaches 360 degrees, nothing shows it | No rule said a visible claim must be shown | A claim a camera could see gets a product shot that shows it | Fixed (not tested yet) |
| Le Duo: she does a straightening move but the hair ends up curly | The plan dropped the page's "twist while sliding" step | Keep the page's wording of the action. Did not work | Known limit |
| Le Duo: the tool just spins by itself, "the lowest form of B-roll" | Nothing banned the product spinning on its own | The product never spins or flips. A hand or the product must do something | Fixed (not tested yet) |
| Phone case: both sides of the phone show the back camera (3 clips) | The video model only sees one side, so it invents the hidden side | Never turn the product. The side facing the camera stays facing it | Came back |
| Shower cleaner: the soap scum problem was a talking shot, not shown | There was no kind of product shot for showing the problem | New product shot kind: shows the problem | Partly fixed |
| Shower cleaner: no real before/after photo, so no result to show | The shop has none and the app made the scene anyway | No scene shows a thin film without the user's photo | Known limit |
| Shower cleaner: before and after look the same | Soap scum is an almost invisible film | Starting picture (a still image the video model starts from) must show the before clearly | Dropped |
| All 5 ads: product shots look hand-held, phone-filmed, for no reason | Our own prompts asked for a casual phone video | Removed that wording. Camera stays still | Fixed (proven later) |

## What we learned
- Our own prompts caused several faults (phone look, too much talking). Read our prompts first.
- The video model invents what it can't see: the hidden side of a phone, a hair change it doesn't understand.
