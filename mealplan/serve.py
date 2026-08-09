#!/usr/bin/env python3
"""
serve.py — local web app for the meal planner. The real solver runs behind it.

    python3 serve.py            then open http://localhost:8770

Stdlib only (beyond what plan.py already needs), so there is nothing new to install.
Every interaction hits the actual LP — this is not a preview of precomputed answers.
"""

import json, math, os, sys, threading, traceback, webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
import plan  # noqa: E402

PORT = int(os.environ.get("PORT", 8770))
_lock = threading.Lock()          # CBC is not reentrant; serialize solves


def fresh():
    return plan.load()


def apply_overrides(people, settings, body):
    if body.get("budget") is not None:
        settings["budget"] = {"mode": "shared", "total": float(body["budget"])}
    for pn, v in (body.get("mass") or {}).items():
        if pn in people:
            people[pn]["max_daily_mass_g"] = float(v) if v else None
    for pn, v in (body.get("targets") or {}).items():
        if pn in people:
            people[pn]["targets"].update({k: float(x) for k, x in v.items()})
    for pn, v in (body.get("tolerance") or {}).items():
        if pn in people:
            people[pn]["tolerance"] = float(v)
    for pn, v in (body.get("dislikes") or {}).items():
        if pn in people:
            people[pn]["dislikes"] = list(v)
    return people, settings


def comp_public(comps, ing):
    out = {}
    for i, c in comps.items():
        out[i] = dict(
            id=i, name=c["name"], cuisine=c["cuisine"], role=c["role"],
            anchor=c.get("anchor"), per100=c["per100"], tags=c["tags"],
            serve=c["serve_g"], unit_g=c.get("unit_g"), yield_g=c["yield_g"],
            keeps=c["keeps_days"], active=c["active_min"], source=c.get("source", ""),
            cost_per_100g=round(plan.cost_per_g(comps, ing, i) * 100, 3),
        )
    return out


def solve_all(body):
    ing, comps, people, settings = fresh()
    people, settings = apply_overrides(people, settings, body)
    for cid in body.get("exclude") or []:
        comps.pop(cid, None)
    n = int(body.get("n", 12))
    seed = int(body.get("seed", 0))
    must = [m for m in (body.get("force") or []) if m in comps]

    menu = body.get("menu")
    if menu:
        menu = [m for m in menu if m in comps]
        _, info = plan.score_menu(comps, ing, menu, settings, people)
        feas, broke = True, {}
        for pn, p in people.items():
            ok, _, miss = plan.plate(p, comps, menu)
            if not ok:
                feas = False
                broke[pn] = miss
    else:
        menu, info, feas, broke = plan.choose_menu(
            comps, ing, people, settings, n=n, seed=seed, must=must)

    weeks, demand = plan.build_week(comps, people, settings, menu)

    # ---- split the cooking across sessions by WHICH DAYS each batch feeds ----
    cook_days = settings.get("cook_days", [0, 3])

    def session_of(day):
        s_ = 0
        for k, start in enumerate(cook_days):
            if start <= day:
                s_ = k
        return s_

    sess_demand = {}          # (component, session) -> grams
    for wk in weeks.values():
        for d, pl in enumerate(wk):
            si = session_of(d)
            for cid, g in pl.items():
                sess_demand[(cid, si)] = sess_demand.get((cid, si), 0) + g

    batches, cook = {}, []
    for i in menu:
        need = demand.get(i, 0)
        per_sess = []
        for si in range(len(cook_days)):
            d_ = sess_demand.get((i, si), 0)
            per_sess.append(math.ceil(d_ / comps[i]["yield_g"] - 1e-9) if d_ else 0)
        b = sum(per_sess)
        batches[i] = b
        if b:
            cook.append(dict(id=i, name=comps[i]["name"], need=round(need), batches=b,
                             per_session=per_sess,
                             made=b * comps[i]["yield_g"],
                             leftover=round(b * comps[i]["yield_g"] - need),
                             active=comps[i]["active_min"] * b,
                             keeps=comps[i]["keeps_days"],
                             ingredients={k: round(v * b)
                                          for k, v in comps[i]["ingredients"].items()}))

    sess_min = [plan.cook_minutes(comps, settings,
                                  {c["id"]: c["per_session"][si] for c in cook})
                for si in range(len(cook_days))]

    cooked = [i for i in menu if batches.get(i)]
    rows, wp, wt = plan.purchase(comps, ing, cooked, batches)
    bought = plan.menu_cost(comps, ing, cooked, batches)
    shares, eaten = plan.attribute(comps, ing, weeks, bought)

    shop = [dict(name=n_, need=g, units=u, pack=pk, pack_h=plan.human_pack(pk),
                 left=l, perishable=per, keeps=k,
                 cost=round(u * ing[n_]["cost"], 2))
            for n_, g, u, pk, l, per, k in rows]

    vol = {}
    for pn, wk in weeks.items():
        ms = [sum(pl.values()) for pl in wk if pl]
        vol[pn] = dict(avg=round(sum(ms) / len(ms)) if ms else 0,
                       lo=min(ms) if ms else 0, hi=max(ms) if ms else 0,
                       cap=people[pn].get("max_daily_mass_g"))

    return dict(
        menu=menu, feasible=feas,
        broke={k: {m: v for m, v in val.items()} for k, val in (broke or {}).items()},
        info=info, components=comp_public(comps, ing),
        people={pn: dict(targets=p["targets"], tolerance=p["tolerance"],
                         exclude=p.get("exclude") or [],
                         dislikes=p.get("dislikes") or [],
                         mass_cap=p.get("max_daily_mass_g"),
                         kcal=plan.kcal_of(p["targets"])) for pn, p in people.items()},
        weeks={pn: [dict(items=pl) for pl in wk] for pn, wk in weeks.items()},
        cook=cook, shop=shop, batches=batches,
        cost=dict(bought=round(bought, 2), eaten=round(sum(eaten.values()), 2),
                  ceiling=plan.budget_ceiling(settings, people),
                  shares={k: round(v, 2) for k, v in shares.items()}),
        # total = SUM of sessions, not one amortised run: you set the kitchen up twice
        cook_minutes=sum(sess_min),
        session_minutes=sess_min,
        active_budget=settings.get("active_min_budget"),
        waste=dict(perishable=wp, total=wt), volume=vol,
        settings=dict(days=settings["days"], cook_days=settings.get("cook_days", [0, 3]),
                      budget=settings.get("budget", {})),
    )


def replate(body):
    """Re-solve ONE person's ONE day with some portions pinned."""
    ing, comps, people, settings = fresh()
    people, settings = apply_overrides(people, settings, body)
    pn = body["person"]
    menu = [m for m in body["menu"] if m in comps]
    locked = {k: float(v) for k, v in (body.get("locked") or {}).items() if k in comps}
    ok, pl, miss = plan.plate(people[pn], comps, menu, locked=locked)
    return dict(ok=ok, items=pl, miss=miss)


def frontier(q):
    ing, comps, people, settings = fresh()
    lo = int(q.get("lo", [250])[0]); hi = int(q.get("hi", [600])[0])
    step = int(q.get("step", [25])[0]); n = int(q.get("n", [12])[0])
    pts = []
    for cap in range(lo, hi + 1, step):
        st = dict(settings); st["budget"] = {"mode": "shared", "total": cap}
        menu, info, feas, _ = plan.choose_menu(comps, ing, people, st, n=n)
        pts.append(dict(budget=cap,
                        spend=round(plan.menu_cost(comps, ing, menu,
                                                   people=people, settings=st), 2),
                        dishes=len([i for i in menu
                                    if comps[i]["role"] in ("main", "starch")]),
                        cuisines=info["cuisines"], waste=info["waste_perishable"],
                        feasible=feas))
    return dict(points=pts)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200, ctype="application/json"):
        b = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path in ("/", "/index.html"):
                return self._send((ROOT / "app.html").read_bytes(),
                                  ctype="text/html; charset=utf-8")
            if u.path == "/api/frontier":
                with _lock:
                    return self._send(frontier(parse_qs(u.query)))
            if u.path == "/favicon.ico":
                return self._send(b"", 204, "image/x-icon")
            if u.path == "/api/library":
                ing, comps, people, settings = fresh()
                return self._send(dict(components=comp_public(comps, ing)))
            self._send({"error": "not found"}, 404)
        except Exception:
            traceback.print_exc()
            self._send({"error": traceback.format_exc()}, 500)

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        try:
            with _lock:
                if u.path == "/api/plan":
                    return self._send(solve_all(body))
                if u.path == "/api/replate":
                    return self._send(replate(body))
            self._send({"error": "not found"}, 404)
        except Exception:
            traceback.print_exc()
            self._send({"error": traceback.format_exc()}, 500)


if __name__ == "__main__":
    print(f"\n  mealplan → http://localhost:{PORT}\n  ctrl-c to stop\n")
    try:
        webbrowser.open(f"http://localhost:{PORT}")
    except Exception:
        pass
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
