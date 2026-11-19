# Working around the video model

Boreal-H3, Creatify's video model, films every clip. It's good, but it breaks physics a lot. Breaking things is normal for AI video right now, so most of my work went into finding what it does wrong and building around it. These are the workarounds that matter most. All 21 quirks I found, with the code for each: [h3-limits.md](../h3-limits.md).

## A drawn starting picture for every clip

- **What it is.** Before filming, the scene agent draws the clip's first frame: the presenter, the room and the product. A check looks at it, and only then the video model films from it.
- **Why.** If we get the starting picture right and give the right instructions, it's hard for the video model to break the physics. The starting picture sets the direction and aligns the video model with what we want. The risk is, if the starting picture is wrong, the whole clip goes wrong. So every one gets checked before any money is spent on filming.
- **Proof.** In round 7 I tried sending the real photos from the product page instead, with no drawn picture. The video model copied everything in them: backgrounds, clothes, other people. Only 4 of 15 ads worked, and 14 of 27 product shots. With a drawn and checked starting picture (round 8), 26 of 27 product shots worked.

## End pictures

- **What it is.** The scene agent can also draw the clip's last frame, like the finished makeup or the clean spot. The video model always ends exactly on it.
- **Why.** The video model doesn't know how a product looks after it's used, so it guesses. Foundation came out wrong on her face, and a cleansing gel turned into white lather. If we show it the end, it doesn't have to guess.
- **Proof.** On scenes that had failed, 14 of 20 tries worked with an end picture, then 8 of 11.
- **Cost.** A wrong end picture pushes the clip wrong. It's the first thing in [what it still can't do](../../README.md#what-it-still-cant-do).

## One movement per clip

- **What broke.** A toilet cleaner clip asked for squeeze, scrub, wait and flush. The toilet flushed before the button was pressed.
- **Why.** The video model can't make the change between actions, so it jumps from the first state to the last.
- **What I did.** One product movement per clip, and the voice carries the other steps. Something with two states, like before and after, gets two clips with a cut.
- **Proof.** Plans with 2 or more actions in one clip went from 18 of 54 to 0 of 61.

## Contents never change on screen

- **What broke.** The video model makes up how gel, foam or serum looks and changes. A serum grew into a thicker pink patch instead of thinning out.
- **What I did.** Contents are shown coming out, or already finished, but never changing on screen. The before and the after get their own clips with a cut.
- **Proof.** Putting a product on skin never passed: Glow Recipe (round 6), Krave (rounds 8 to 10), Bondi (rounds 12 and 13). The finished-result way passed my test on Nov 10.

## The whole product in the starting picture

- **What broke.** The video model only gets the starting picture. Any part it can't see, cut off by the frame or hidden under a hand, it makes up when that part comes into view.
- **What I did.** A product small enough is shown whole, from one side, and the clip never shows a part that was hidden.
- **Proof.** Of the hidden-part fails I graded, 3 of 14 were parts cut off by the frame and 10 were hidden parts showing up later. In a picture test on 17 products, I passed 63 of 65 starting pictures. This one is new (Nov 17), so it's not proven on full ads yet.
