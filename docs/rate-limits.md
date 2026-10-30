# API rate limits (checked 2026-10-08; pictures and Creatify updated 2026-10-20)

Sources: **API** = returned by the service itself (rate-limit headers on one cheap call, or an account endpoint). **Docs** = provider's own published page for our plan. No load testing was done.

| Service | Limit | Applies to | Source | Safe setting for test scripts |
|---|---|---|---|---|
| OpenAI pictures `gpt-image-2.5-sunburst` | **20 pictures/min** (Julian, 2026-10-20: "It is just 20 images a minute") | draw_person, make_starting_picture, make_reference_photo_picture, make_before_picture (draw and edit) | Julian's own number for his OpenAI account, not re-checked (his words: no need). Old 2/min was the Azure deployment (API header `x-ratelimit-limit-requests: 2`, 2026-10-08); pictures moved to OpenAI's own API | **Up to 20 pictures/min, no app-side limiter** (no semaphore or picture lock in the app, checked 2026-10-20). App retries a 429 only after 2 s + 8 s (settings.py:190), so a burst over 20 in one minute can still fail a picture. |
| Azure OpenAI text `gpt-6.1-sol`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-6-luna`, `gpt-5-mini` | **1,000 requests/min, 1,000,000 tokens/min, per model** (read from API headers 2026-10-09 19:15; was 150/150k) | all chat calls | API: `x-ratelimit-limit-requests: 150`, `x-ratelimit-limit-tokens: 150000`, renewal 60 s, East US 2 | Not a constraint at 10 ads at once. Azure checks RPM in 1 s or 10 s slices (Docs: learn.microsoft.com quota page), so avoid >2-3 calls in the same second per model. |
| Firecrawl (Free plan) | **2 concurrent browsers; /scrape 10 req/min** | page read, marked screenshot, product record (3 calls per product) | API: `/v2/team/queue-status` → `maxConcurrency: 2`. Docs: docs.firecrawl.dev/rate-limits ("Concurrent browsers: 2", "/scrape: 10"). Credits: 9,392 left (API) | ~3 products/min. Extra jobs queue (not fail), but queue time counts against the timeout. |
| Inworld (On-Demand plan) | **5 concurrent requests** | TTS + voice design | Docs: inworld.ai/pricing ("On-Demand \| 5"). API returns no limit headers | **5 ads at once max** (matches the old "429 at ~6 ads"). |
| ElevenLabs (Free tier) | 8 concurrent speech-to-text; 10,000 credits/month (2,918 used) | transcribe_line (Scribe v2) | API: `/v1/user/subscription` (tier free, credits). Docs: concurrency 8 (help-center page). | Concurrency fine. **Credits are the risk**: Docs say STT = 330 credits/min, so ~21 min of audio left this month (inferred). |
| HeyGen (wallet / Pay-As-You-Go) | 10 concurrent video jobs; write 10 req/s; status checks 500/min | talking clips | Docs: developers.heygen.com/docs/usage-limits ("Pay-As-You-Go \| 10"). API: wallet $19.07, no limit returned | Up to 10 talking clips at once. |
| Creatify (Boreal-H3) | **no published number** (re-checked 2026-10-20 after Julian's tier upgrade) | B-roll clips | API: `GET /api/remaining_credits/` → 1,953 credits, no rate-limit headers in the reply (2026-10-20 02:13 UTC). Docs: docs.creatify.ai/billing.md lists API Starter 500 / API Pro 2,000 credits a month, no rate or concurrency number for any tier; faq.md only says "429 rate limited" and sends volume >2k renders/month to sales (inferred: 1,953 credits = API Pro) | No cap in the scripts: all clips fire at once. The adapter treats a 429 as worth retrying (creatify_adapter.py:142), 2 s + 8 s. Only a real run shows the true limit; ask api@creatify.ai for the number if 429s show up. |
| fal.ai | 2 concurrent requests (new accounts; rises to 40 as credits are bought) | music, file upload | Docs: fal.ai/docs/model-apis/faq. API usage endpoint refused our key (403) | 2 at once; extra requests queue. |
| Langfuse (Hobby) | ingestion 1,000 req/min; general API 30 req/min | trace logging | Docs: langfuse.com/faq/all/api-limits | Not a constraint. |
| AWS S3 | 3,500 writes/s, 5,500 reads/s per prefix | file store | Docs: AWS S3 performance guide | Not a constraint. |

## What this means for parallel runs (2026-10-20)
- Pictures: 20/min on OpenAI. An ad needs ~6 pictures (inferred), so 22 ads need ~130 pictures, ~7 min of picture time if spread evenly. Bursts above 20 in one minute can fail after the 2 s + 8 s retries.
- Creatify: no known cap; everything fires at once.
- Still capped, unchanged: Inworld 5 concurrent voice requests (app gives up on a 429 after 2 retries), Firecrawl 2 concurrent / 10 scrapes a minute (the app waits out Firecrawl 429s, firecrawl.py:35-38).

`scripts/ad-runs/run.py` starts ads 20 s apart for Firecrawl; nothing else is throttled in the scripts.
