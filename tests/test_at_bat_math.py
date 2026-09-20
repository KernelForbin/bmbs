"""
Direct checks for the at-bats-remaining math in index.html: negBinomPmf(),
atBatEstimate() and remainingOutsForSide() -- the negative-binomial model
behind "~2.1 AB left * 100% chance of another AB" on a leg's live-context line.

Every existing test that touches this only confirms SOME number renders
inside a fully-mocked slate; none of them check that the number is actually
right. This file calls the three functions directly (they're page-global
`function` declarations, so `window.negBinomPmf` etc. exist without any
export) against a blank load of index.html with no slate and no MLB
polling -- nothing here depends on tickets/results state, only on the
explicit parameters each function takes.

The cross-check grid compares the JS output to an independently-written
Python reference (closed-form via math.comb, not a port of the JS's
iterative coefficient loop) across a spread of (d, r, p) -- catching a loop-
bound or threshold bug that a handful of hand-picked cases could miss. A few
cases are also hand-verified in isolation (the deterministic r<=0 and
d=0,r=1 shortcuts, the geometric-distribution identity at r=1, and that the
estimate moves the right direction as the league out rate changes).

    python tests/test_at_bat_math.py
"""
import math
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

INDEX = Path(__file__).resolve().parent.parent / "index.html"
failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------- independent Python reference ----------------

def neg_binom_pmf(j, r, p):
    if j < 0 or r <= 0:
        return 1.0 if j == 0 else 0.0
    return math.comb(j + r - 1, j) * (p ** r) * ((1 - p) ** j)


def at_bat_estimate(d, r, p):
    if r <= 0:
        return {"probAtLeastOne": 0.0, "expected": 0.0}

    def prob_at_least_k(k):
        threshold = (d + 1 + 9 * (k - 1)) - r
        if threshold <= 0:
            return 1.0
        cum = sum(neg_binom_pmf(j, r, p) for j in range(threshold))
        return max(0.0, min(1.0, 1 - cum))

    prob1 = prob_at_least_k(1)
    expected = sum(prob_at_least_k(k) for k in range(1, 7))
    return {"probAtLeastOne": prob1, "expected": expected}


def handler(route, request):
    # No slate, no schedule, nothing to poll -- proven safe in test_page.py's
    # "no slate files at all" scenario. These functions don't touch TICKETS or
    # RESULTS anyway; this is just enough for init() to settle without error.
    url = request.url
    if url.endswith("/index.html"):
        return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=INDEX.read_bytes())
    if url.endswith(".json"):
        return route.fulfill(status=404, body="")
    if "/api/v1/schedule" in url:
        return route.fulfill(status=200, content_type="application/json", body='{"dates":[]}')
    if "fonts.googleapis.com" in url or "fonts.gstatic.com" in url:
        return route.fulfill(body="", content_type="text/css")
    return route.abort()


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/*", handler)
    page.goto("http://bmbs.test/index.html")
    page.wait_for_function("typeof negBinomPmf === 'function' && typeof atBatEstimate === 'function'")

    def js_pmf(j, r, prob):
        return page.evaluate("([j,r,p]) => negBinomPmf(j,r,p)", [j, r, prob])

    def js_estimate(d, r, prob):
        return page.evaluate("([d,r,p]) => atBatEstimate(d,r,p)", [d, r, prob])

    def js_outs(inning, half, outs, side):
        return page.evaluate("([i,h,o,s]) => remainingOutsForSide(i,h,o,s)", [inning, half, outs, side])

    # ---------------- A. negBinomPmf: deterministic edges ----------------
    check("A1 j<0 -> 0, regardless of r", js_pmf(-1, 5, 0.685) == 0)
    check("A2 r<=0 and j==0 -> 1 (even r itself is 0 or negative)", js_pmf(0, 0, 0.5) == 1 and js_pmf(0, -3, 0.5) == 1)
    check("A3 r<=0 and j!=0 -> 0", js_pmf(2, 0, 0.5) == 0)
    geo = js_pmf(3, 1, 0.3)
    check("A4 r=1 is the geometric distribution: P(j)=(1-p)^j * p exactly",
          abs(geo - (0.3 * 0.7 ** 3)) < 1e-12, geo)

    # ---------------- B. negBinomPmf: cross-check against the Python reference ----------------
    grid_ok = True
    for r in (1, 2, 3, 5, 9):
        for prob in (0.2, 0.315, 0.685, 0.9):
            for j in (0, 1, 2, 5, 10, 20):
                js_v, py_v = js_pmf(j, r, prob), neg_binom_pmf(j, r, prob)
                if abs(js_v - py_v) > 1e-9:
                    grid_ok = False
                    print(f"      mismatch j={j} r={r} p={prob}: js={js_v} py={py_v}")
    check("B1 matches an independent (math.comb-based) reference across a grid of j/r/p", grid_ok)

    total = sum(js_pmf(j, 3, 0.315) for j in range(200))
    check("B2 the pmf sums to ~1 over its support (a formula bug would drift this)", abs(total - 1.0) < 1e-9, total)

    # ---------------- C. atBatEstimate: deterministic edges ----------------
    check("C1 r<=0 -> zeroed out regardless of d or p", js_estimate(3, 0, 0.685) == {"probAtLeastOne": 0, "expected": 0})
    guaranteed = js_estimate(0, 1, 0.685)
    check("C2 up right now (d=0) with at least 1 out left -> guaranteed at least one more PA, independent of p",
          guaranteed["probAtLeastOne"] == 1, guaranteed)
    guaranteed2 = js_estimate(3, 5, 0.9)
    check("C2b guaranteed whenever outs-left already exceed the batter's distance (r >= d+1)",
          guaranteed2["probAtLeastOne"] == 1, guaranteed2)

    # ---------------- D. atBatEstimate: cross-check against the Python reference ----------------
    grid_ok = True
    for d in (0, 1, 2, 3, 5, 8):
        for r in (1, 2, 3, 5, 9, 15, 27):
            for prob in (0.4, 0.685, 0.9):
                js_v, py_v = js_estimate(d, r, prob), at_bat_estimate(d, r, prob)
                if abs(js_v["probAtLeastOne"] - py_v["probAtLeastOne"]) > 1e-9 or abs(js_v["expected"] - py_v["expected"]) > 1e-9:
                    grid_ok = False
                    print(f"      mismatch d={d} r={r} p={prob}: js={js_v} py={py_v}")
    check("D1 matches the independent reference across a grid of d/r/p (24 games' worth of realistic values)", grid_ok)

    # ---------------- E. atBatEstimate: the estimate moves the right direction ----------------
    # Higher league out rate (p) means outs pile up faster, so a batter several
    # slots away is LESS likely to get another look -- not more. d=8,r=3 is a
    # real long-shot case (probability strictly between 0 and 1, so the
    # direction is actually visible rather than clipped at 0 or 1).
    low_out_rate = js_estimate(8, 3, 0.4)
    real_out_rate = js_estimate(8, 3, 0.685)
    high_out_rate = js_estimate(8, 3, 0.9)
    check("E1 probAtLeastOne strictly decreases as the out rate rises",
          low_out_rate["probAtLeastOne"] > real_out_rate["probAtLeastOne"] > high_out_rate["probAtLeastOne"] > 0,
          (low_out_rate, real_out_rate, high_out_rate))
    check("E2 expected PAs remaining moves the same direction",
          low_out_rate["expected"] > real_out_rate["expected"] > high_out_rate["expected"])

    # ---------------- F. remainingOutsForSide: every branch, hand-computed ----------------
    check("F1 no inning at all -> 0, regardless of everything else", js_outs(0, "Top", 1, "away") == 0 and js_outs(None, "Bottom", 0, "home") == 0)
    check("F2 Top of the 5th, 1 out: away (batting) gets its own outsLeft + 3 full innings ahead",
          js_outs(5, "Top", 1, "away") == 2 + 3 * 4)
    check("F3 ...home gets a full 3 outs this half (hasn't batted yet) + the same innings ahead",
          js_outs(5, "Top", 1, "home") == 3 + 3 * 4)
    check("F4 Top of the 9th (last inning): no full innings left over", js_outs(9, "Top", 0, "away") == 3 + 0)
    check("F5 Middle of the 5th: away is done for the half (0 left), home hasn't started (full 3 + innings ahead)",
          js_outs(5, "Middle", 2, "away") == 3 * 4 and js_outs(5, "Middle", 0, "home") == 3 + 3 * 4)
    check("F6 Bottom of the 5th, 2 outs: away gets only future innings (already batted this frame)",
          js_outs(5, "Bottom", 2, "away") == 3 * 4)
    check("F7 ...home gets its own outsLeft + future innings", js_outs(5, "Bottom", 2, "home") == 1 + 3 * 4)
    check("F8 Bottom of the 9th (walk-off territory): home's own outsLeft, no innings left over",
          js_outs(9, "Bottom", 1, "home") == 2 + 0)
    check("F9 'End' of the 5th treated as just before the 6th's top begins",
          js_outs(5, "End", 0, "away") == 3 + 3 * 3 and js_outs(5, "End", 0, "home") == 3 * 3)
    check("F10 an unrecognized half-state falls back to the same 'about to start next inning' rule as End",
          js_outs(5, "Weird", 0, "away") == js_outs(5, "End", 0, "away"))

    check("no script errors from any of this", not errors, str(errors))
    browser.close()

print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    sys.exit(1)
print("all at-bat math checks passed")
