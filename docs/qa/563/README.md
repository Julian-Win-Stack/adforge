# Whole-version restoration QA — #563

Used the running Vite app and Django API with the real producer/scene tools and assembly. A temporary pytest live server loaded the existing `shown` fixture from its first product-link chat message, with quality checks on and only outside services faked at the gateway. No paid service was called. The videos and pictures are deliberately plain test fixtures.

In Chromium, I sent scene 2 the note **“Show the mug on a shelf.”** Its scene agent changed the action and filmed a new version. I previewed v1 and selected **Use this in the ad**, selected the newer version through chat, then typed **“Put the old one back”**. Both restorations kept the original file and restored the original action. The reassembled ad played successfully (`paused=false`, `readyState=4`, playback time advanced to 0.77s). The live-server fixture also asserts that outside-service calls do not increase during these swaps.

The first pass exposed a repeated full stop in the restoration notice and a hidden version number under the thumbnail description. Both were fixed, then the browser flow was repeated. The picker now shows only the line/action differences from the version in the ad; selecting a version shows its complete line and action.

Screenshots:

- [Before card swap](01-before-card-swap.png)
- [Preview before choosing](02-card-preview.png)
- [After free card swap](03-after-card-swap.png)
- [Before typed chat restoration](04-before-chat-restore.png)
- [Restored ad playing](05-after-chat-restore.png)
- [Typed request and restored row notice](06-chat-row-restored.png)
- [Final picker showing the differing action](07-differences-only-picker.png)

`supermatt:qa` was unavailable in the local/cloud skill catalogs. Used the cloud runtime browser workflow with Playwright and installed Chromium instead. The temporary live-server harness was not committed.
