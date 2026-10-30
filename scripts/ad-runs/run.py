"""Runs a batch of ads on the Azure VM, each started with just its product link.

  python3 run.py <run-dir> --name "R8" --only <key>   # one ad
  python3 run.py <run-dir> --name "R8"                # every ad in <run-dir>/links.json not started yet

<run-dir>/links.json: [{"key": "n01-some-product", "url": "https://..."}, ...].

- Drives the deployed app's own API (http://127.0.0.1:8000, the VM-only door), so every ad is
  a normal session in the Azure app. Each session is renamed "<name> <key>" so it is easy to find.
- Starts everything at once, --stagger seconds apart (default 20: Firecrawl allows 10 scrapes a
  minute and an ad makes 3).
- Money: sums cost_usd of every ModelCall made since the first start (every provider the app
  prices). At --pause-at it stops starting ads and presses pause on every unfinished one, so
  steps already running finish but nothing new starts. The budget is Julian's call.
- One ad past --ad-hold dollars is paused and counts as stopped (a producer can retry a failing
  scene up to its tool-call cap).
- Writes <run-dir>/ads.json (key -> session id) and run.json as it goes. Safe to stop and rerun:
  an ad already in ads.json is not started twice. Ends when every started ad has a finished ad
  or is listed in <run-dir>/skip.txt (one key per line: an ad that stopped for good).
"""
import argparse, datetime, json, os, subprocess, time, urllib.request

B = "http://127.0.0.1:8000/api"
REPO = "/srv/adforge"
ap = argparse.ArgumentParser()
ap.add_argument("run_dir"); ap.add_argument("--name", required=True)
ap.add_argument("--pause-at", type=float, required=True, help="dollars: pause every ad here")
ap.add_argument("--ad-hold", type=float, default=25.0, help="dollars: pause one ad past this")
ap.add_argument("--only"); ap.add_argument("--stagger", type=int, default=20)
a = ap.parse_args()
R = a.run_dir
PAUSE_AT, AD_HOLD = a.pause_at, a.ad_hold


def req(m, u, d=None):
    r = urllib.request.Request(B + u, method=m, data=json.dumps(d).encode() if d is not None else None,
                               headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=60))


def shell(code):
    out = subprocess.run(["docker", "compose", "-f", "deploy/compose.yml", "exec", "-T", "backend",
                          "python", "manage.py", "shell", "-c", code],
                         cwd=REPO, capture_output=True, text=True, timeout=180)
    if out.returncode:
        raise RuntimeError(out.stderr[-500:])
    return out.stdout


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(p, d):
    return json.load(open(p)) if os.path.exists(p) else d


def spent(since):
    code = ("from django.db.models import Sum\nfrom gateway.models import ModelCall\n"
            f"t = ModelCall.objects.filter(created_at__gte='{since}').aggregate(s=Sum('cost_usd'))['s'] or 0\n"
            "print('SPENT', float(t))")
    return float(shell(code).split()[-1])


def per_ad(since):
    code = ("from django.db.models import Sum\nfrom gateway.models import ModelCall\n"
            f"for r in ModelCall.objects.filter(created_at__gte='{since}').values('session_id').annotate(s=Sum('cost_usd')): print('AD', r['session_id'], float(r['s'] or 0))")
    return {l.split()[1]: float(l.split()[2]) for l in shell(code).splitlines() if l.startswith("AD ")}


def finished():
    code = ("from jobs.models import ProducedItem as P\n"
            "for s in set(P.objects.filter(kind=P.Kind.FINISHED_AD).values_list('job__session_id', flat=True)): print('DONEID', s)")
    return {l.split()[1] for l in shell(code).splitlines() if l.startswith("DONEID")}


links = json.load(open(os.path.join(R, "links.json")))
if a.only:
    links = [l for l in links if l["key"] == a.only]
ads_p, run_p = os.path.join(R, "ads.json"), os.path.join(R, "run.json")
ads = load(ads_p, {}); run = load(run_p, {"name": a.name, "started": now(), "ended": None, "paused_at": None})
json.dump(run, open(run_p, "w"), indent=1)
paused = run["paused_at"] is not None
while True:
    try:
        done = finished()
        skip = set(open(os.path.join(R, "skip.txt")).read().split()) if os.path.exists(os.path.join(R, "skip.txt")) else set()
        live = [k for k, s in ads.items() if s not in done and k not in skip]
        todo = [] if paused else [l for l in links if l["key"] not in ads]
        cost = spent(run["started"])
        if cost >= PAUSE_AT and not paused:
            paused = True; run["paused_at"] = now(); json.dump(run, open(run_p, "w"), indent=1)
            for k in live:
                req("POST", f"/sessions/{ads[k]}/pause/", {})
            print(f"{now()} CEILING: ${cost:.2f} spent >= ${PAUSE_AT:.0f}; paused {len(live)} ads, starting no more. Tell Julian.", flush=True)
        held = set(run.get("held", []))
        costs = per_ad(run["started"])
        for k in live:
            if k not in held and costs.get(ads[k], 0) > AD_HOLD:
                req("POST", f"/sessions/{ads[k]}/pause/", {}); held.add(k); run["held"] = sorted(held)
                json.dump(run, open(run_p, "w"), indent=1)
                print(f"{now()} HELD {k}: ${costs[ads[k]]:.2f} on this ad > ${AD_HOLD:.0f}; paused. Tell Julian.", flush=True)
        live = [k for k in live if k not in held]
        for k in live:  # the first message names a session; put the run's name on it
            s = req("GET", f"/sessions/{ads[k]}/")
            if not (s.get("name") or "").startswith(f"{a.name} "):
                req("PATCH", f"/sessions/{ads[k]}/", {"name": f"{a.name} {k}"})
        if not todo and not live:
            run["ended"] = now(); json.dump(run, open(run_p, "w"), indent=1)
            print(f"{now()} ALL DONE, ${cost:.2f} spent", flush=True); break
        if todo:
            l = todo[0]
            sid = req("POST", "/sessions/", {})["id"]
            req("POST", f"/sessions/{sid}/messages/", {"text": l["url"]})
            ads[l["key"]] = sid; json.dump(ads, open(ads_p, "w"), indent=1)
            print(f"{now()} STARTED {l['key']} {sid} (live {len(live)+1}, left {len(todo)-1}, spent ${cost:.2f})", flush=True)
            time.sleep(a.stagger); continue
        print(f"{now()} live {len(live)}, done {len(done & set(ads.values()))}, spent ${cost:.2f}", flush=True)
    except Exception as e:  # a hiccup in the API or docker must not stop the money watch
        print(f"{now()} run error {e}", flush=True)
    time.sleep(60)
