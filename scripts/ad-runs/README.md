# Ad-run scripts

Run a batch of real ads on the Azure VM, from the VM (they call `http://127.0.0.1:8000/api`). How to get on the VM and deploy: `docs/azure.md`. Pace runs by `docs/rate-limits.md`.

1. Get Julian's OK on the cost (a full ad is about $5).
2. Several ads at once need the one-busy-session check off: on the VM only, add `return False` as the first line of `another_session_is_busy` in `backend/agents/tasks.py`, then `deploy/deploy.sh` with no argument. Never commit that change.
3. Make a run folder outside the repo with `links.json`: `[{"key": "n01-some-product", "url": "https://..."}]`.
4. Run, each in its own terminal:

| Script | What it does |
|---|---|
| `python3 run.py <run-dir> --name R8 --pause-at 235` | Starts every ad 20 s apart, names sessions "R8 <key>", pauses all ads at the budget and one ad past `--ad-hold` ($25) |
| `python3 watch.py <run-dir>` | Prints RED on a red notice and QUIET when an ad waits on the owner or stalls |
| `python3 status.py <run-dir> [full]` | Last message of each ad |
| `python3 answer.py <run-dir> <key> "<text>"` | Answers as the shop owner; logs to `owner-answers-live.log` |
| `python3 ad_warnings.py <run-dir>/ads.json warnings.md` | Every red and grey warning; exits 1 on any red. Run it at the end of every run and report the reds to Julian before he grades |
