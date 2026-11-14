# Script captions without a clip word check

Approved by Julian Nov 14 2026 in [#656](https://github.com/Julian-Win-Stack/adforge/issues/656).
Supersedes the word-check decisions of [0012](0012-every-scene-made-with-h3.md); its other
H3 decisions stand.

Across rounds 14, 15 and baseline-2, 19 of 196 checked clips failed the word check, across
seven scenes. Retrying unchanged inputs fixed only two scenes. In round 14, Mighty Patch
scene 3 passed the clip judge on five of six tries but always said "junk" for "gunk", so
no ad was made. A Furbo clip with a spoken slip reached an ad Julian passed. Viewers read
the captions, which can carry the script even when the voice slips.

Remove both the tidied exact comparison and the model question about heard words. Only
the clip judge and, for a run the owner requested, its fix check decide whether a try passes.
No word-check result, developer notice or owner request to listen is produced. Keep
transcription for caption timing, leading-word trimming and pace notices. When transcription
fails, keep the clip with the existing developer failure notice and no timed captions.

Captions always show the scene's script line, timed from its transcript. A previous clip
version's preview keeps that version's script. Stored word results are ignored by verdicts,
scene cards, captions and free version use; no migration is needed. Evals read heard words
from the clip's transcript, and first-try pass counts no longer require a word pass. The
shared text tidier stays for planning and evals.

Keeping a check that accepts small slips or correcting only names still spends on comparisons
and can exhaust a scene's tries without improving the ad. We accept that a spoken word may
differ from the script on screen. Revisit if Julian's grades show that such slips make ads
unacceptable despite their captions. The planning fact check remains in place.
