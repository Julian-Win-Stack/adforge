# Ad-run scripts

Run a batch of real ads on the Azure VM, from the VM (they call `http://127.0.0.1:8000/api`). How to get on the VM and deploy: `docs/azure.md`. Pace runs by `docs/rate-limits.md`.

1. State the cost; Julian’s run request is his OK.
2. Several ads at once need the one-busy-session check off: set `ONE_AD_AT_A_TIME=0` in the VM's `deploy/.env`, then `deploy/deploy.sh` with no argument so the containers read it. Set it back to `1` (or remove it) after the run.
3. Make a run folder outside the repo with `links.json`: `[{"key": "n01-some-product", "url": "https://..."}]`.
4. Run, each in its own terminal:

| Script | What it does |
|---|---|
| `python3 run.py <run-dir> --name R8 --pause-at 235` | Starts every ad 20 s apart, names sessions "R8 <key>", pauses all ads at the budget and one ad past `--ad-hold` ($25). Refuses to start on a down API, unset key or busy app; a setup refusal mid-run prints `STOP:`, appends to `<run-dir>/STOP` and starts no new ad |
| (in `run.py`) | Answers each ad's plan card, and every newer card, with the agent's picks (`--plan N` picks plan N), printing `CARD <key>: ...`; past 4 cards it prints `STOP: <key>: plan card shown 5 times`. `--style person_talking` or `--style product_shots` pins the style in the first message. An ad with no card `--card-wait` minutes (15) after its link prints `STOP: <key>: no plan card`, records a `no_plan_card` notice and starts no new ad |
| `python3 watch.py <run-dir>` | Prints RED on a red notice and QUIET when an ad waits on the owner or stalls |
| `python3 status.py <run-dir> [full]` | Last message of each ad |
| `python3 answer.py <run-dir> <key> "<text>"` | Answers as the shop owner; logs to `owner-answers-live.log` |
| `python3 ad_warnings.py <run-dir>/ads.json warnings.md` | Every red and grey warning; exits 1 on any red. Run it at the end of every run and report the reds to Julian before he grades |
