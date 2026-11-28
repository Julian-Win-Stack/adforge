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
- Plan card: each ad waits on its plan card after the page is read. The script answers it the
  way a shop owner pressing "Use the agent's picks" would (or picks plan --plan N), prints
  "CARD <key>: <the 3 plans>; answered: <what>" and keeps the card in run.json. An ad with no
  card --card-wait minutes after it started (and no plan) prints "STOP: <key>: no plan card",
  records a developer notice (no_plan_card) and starts no new ad.
- Before starting: refuses (exit 1, nothing started) when the API is down, a key the ads need
  is unset, or (on a fresh run) other ads are being made. Mid-run, a call refused for a setup
  reason (no key, wrong key, no money) prints "STOP: <key>: <reason>", appends it to
  <run-dir>/STOP and starts no new ad; ads already started go on under the money watch.
"""
import argparse, datetime, json, os, subprocess, time, urllib.request

B = "http://127.0.0.1:8000/api"
REPO = "/srv/adforge"
ap = argparse.ArgumentParser()
ap.add_argument("run_dir"); ap.add_argument("--name", required=True)
ap.add_argument("--pause-at", type=float, required=True, help="dollars: pause every ad here")
ap.add_argument("--ad-hold", type=float, default=25.0, help="dollars: pause one ad past this")
ap.add_argument("--only"); ap.add_argument("--stagger", type=int, default=20)
ap.add_argument("--plan", type=int, choices=[1, 2, 3], help="answer each plan card with this plan, not the agent's picks")
ap.add_argument("--card-wait", type=float, default=15.0, help="minutes: stop an ad with no plan card by then")
ap.add_argument("--style", choices=["person_talking", "product_shots"], help="pin the ad style in the first message")
a = ap.parse_args()
R = a.run_dir
PAUSE_AT, AD_HOLD = a.pause_at, a.ad_hold
# An ad shown more plan cards than this is stuck: it stops, with a STOP line.
MOST_CARDS = 4
STYLE_PIN = {"person_talking": "Make it a person talking ad.", "product_shots": "Make it a product shots ad."}


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


def answer_card(k, sid):
    """Answer the ad's open plan card, if it has one, a newer card too: gives the card's id
    and what was printed, or None."""
    msgs = req("GET", f"/sessions/{sid}/messages/")
    answered = {m.get("answers_plan_card") for m in msgs}
    cards = [m["plan_card"] for m in msgs if m.get("plan_card")]
    if not cards or cards[-1]["state"] != "open" or cards[-1]["id"] in answered:
        return None
    card = cards[-1]
    plans = next(q for q in card["questions"] if q["key"] == "plan")["choices"]
    sent = {"use_picks": True} if a.plan is None else {"choices": {"plan": a.plan - 1}}
    req("POST", f"/sessions/{sid}/plan-cards/{card['id']}/answer/", sent)
    titles = ", ".join(f"{n}. {p['title']} ({p['style']}, {p['seconds']} s)" for n, p in enumerate(plans, 1))
    return card["id"], f"CARD {k}: {titles}; answered: " + ("the agent's picks" if a.plan is None else f"plan {a.plan}")


def planned_or_carded(sid):
    """Whether the ad got a plan card or has a plan already."""
    msgs = req("GET", f"/sessions/{sid}/messages/")
    if any(m.get("plan_card") for m in msgs):
        return True
    code = ("from jobs.models import Job\n"
            f"j = Job.objects.filter(session_id='{sid}').first()\n"
            "print('HASPLAN', bool(j and (j.chosen_plan is not None or j.scenes.exists())))")
    return "HASPLAN True" in shell(code)


def no_card_notice(sid, text):
    code = ("from chat.models import Message, Session\nfrom jobs import notices\n"
            f"notices.record(Session.objects.get(pk='{sid}'), notices.NO_PLAN_CARD, {text!r}, Message.Level.PROBLEM)")
    shell(code)


def preflight(fresh):
    """One line per reason the run can't start; empty when it can."""
    try:
        req("GET", "/sessions/")
    except Exception as e:
        return [f"the app's API at {B} is not answering: {e}"]
    code = ("from adforge.paid_runs import ad_run_problems\n"
            f"for p in ad_run_problems(idle={fresh}): print('PROBLEM', p)")
    try:
        out = shell(code)
    except Exception as e:
        return [f"the backend container can't be asked: {e}"]
    return [l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("PROBLEM ")]


def refusals(since):
    code = ("from adforge.paid_runs import setup_refusals_since\n"
            f"for s, e in setup_refusals_since('{since}'): print('REFUSED', s, e.splitlines()[0][:300])")
    return [l.split(" ", 2)[1:] for l in shell(code).splitlines() if l.startswith("REFUSED ")]


links = json.load(open(os.path.join(R, "links.json")))
if a.only:
    links = [l for l in links if l["key"] == a.only]
ads_p, run_p = os.path.join(R, "ads.json"), os.path.join(R, "run.json")
ads = load(ads_p, {})
problems = preflight(fresh=not ads)
for p in problems:
    print(f"Cannot run: {p}", flush=True)
if problems:
    raise SystemExit(1)
run = load(run_p, {"name": a.name, "started": now(), "ended": None, "paused_at": None})
run["stopped"], run["watch_from"] = [], now()  # a rerun after the fix starts ads again
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
        stopped = set(run.get("stopped", []))
        keys = {s: k for k, s in ads.items()}
        for sid, why in refusals(run["watch_from"]):
            k = keys.get(sid)
            if k and k not in stopped:
                stopped.add(k); run["stopped"] = sorted(stopped); json.dump(run, open(run_p, "w"), indent=1)
                line = f"STOP: {k}: {why}"
                with open(os.path.join(R, "STOP"), "a") as f:
                    f.write(line + "\n")
                print(f"{now()} {line} (no new ad starts). Tell Julian.", flush=True)
        if stopped:
            todo = []
        held = set(run.get("held", []))
        costs = per_ad(run["started"])
        for k in live:
            if k not in held and costs.get(ads[k], 0) > AD_HOLD:
                req("POST", f"/sessions/{ads[k]}/pause/", {}); held.add(k); run["held"] = sorted(held)
                json.dump(run, open(run_p, "w"), indent=1)
                print(f"{now()} HELD {k}: ${costs[ads[k]]:.2f} on this ad > ${AD_HOLD:.0f}; paused. Tell Julian.", flush=True)
        live = [k for k in live if k not in held]
        cards, starts = run.setdefault("cards", {}), run.setdefault("started_at", {})
        for k in live:
            if k in stopped:
                continue
            if not isinstance(cards.get(k), list):  # a run.json from before every card was answered
                cards[k] = []
            got = answer_card(k, ads[k])
            if got and len(cards[k]) >= MOST_CARDS:
                why = f"plan card shown {len(cards[k]) + 1} times"
                no_card_notice(ads[k], f"The ad-run script got a {why}, so it stopped answering.")
                stopped.add(k); run["stopped"] = sorted(stopped); json.dump(run, open(run_p, "w"), indent=1)
                with open(os.path.join(R, "STOP"), "a") as f:
                    f.write(f"STOP: {k}: {why}\n")
                print(f"{now()} STOP: {k}: {why} (no new ad starts). Tell Julian.", flush=True)
                todo = []
                continue
            if got:
                cards[k].append(got[0]); json.dump(run, open(run_p, "w"), indent=1)
                print(f"{now()} {got[1]}", flush=True)
                continue
            if cards[k]:
                continue
            began = datetime.datetime.fromisoformat(starts.get(k, run["started"]).replace("Z", "+00:00"))
            waited = (datetime.datetime.now(datetime.timezone.utc) - began).total_seconds() / 60
            if waited > a.card_wait and not planned_or_carded(ads[k]):
                why = f"no plan card {waited:.0f} minutes after the link was sent"
                no_card_notice(ads[k], f"The ad-run script got {why}, so it stopped waiting.")
                stopped.add(k); run["stopped"] = sorted(stopped); json.dump(run, open(run_p, "w"), indent=1)
                with open(os.path.join(R, "STOP"), "a") as f:
                    f.write(f"STOP: {k}: no plan card\n")
                print(f"{now()} STOP: {k}: {why} (no new ad starts). Tell Julian.", flush=True)
                todo = []
        for k in live:  # the first message names a session; put the run's name on it
            s = req("GET", f"/sessions/{ads[k]}/")
            if not (s.get("name") or "").startswith(f"{a.name} "):
                req("PATCH", f"/sessions/{ads[k]}/", {"name": f"{a.name} {k}"})
        if not todo and not live:
            run["ended"] = now(); json.dump(run, open(run_p, "w"), indent=1)
            unstarted = len([l for l in links if l["key"] not in ads])
            if stopped:
                print(f"{now()} STOPPED, {unstarted} ads not started (see STOP), ${cost:.2f} spent", flush=True); break
            print(f"{now()} ALL DONE, ${cost:.2f} spent", flush=True); break
        if todo:
            l = todo[0]
            sid = req("POST", "/sessions/", {})["id"]
            first = l["url"] if a.style is None else f"{l['url']} {STYLE_PIN[a.style]}"
            req("POST", f"/sessions/{sid}/messages/", {"text": first})
            ads[l["key"]] = sid; json.dump(ads, open(ads_p, "w"), indent=1)
            run.setdefault("started_at", {})[l["key"]] = now(); json.dump(run, open(run_p, "w"), indent=1)
            print(f"{now()} STARTED {l['key']} {sid} (live {len(live)+1}, left {len(todo)-1}, spent ${cost:.2f})", flush=True)
            time.sleep(a.stagger); continue
        print(f"{now()} live {len(live)}, done {len(done & set(ads.values()))}, spent ${cost:.2f}", flush=True)
    except Exception as e:  # a hiccup in the API or docker must not stop the money watch
        print(f"{now()} run error {e}", flush=True)
    time.sleep(60)
