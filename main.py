"""Forecast GTA VI launch-day Twitch viewers hour by hour (make.fun
gta6-twitch-ccu-forecast-01). Python 3.13, standard library only.

Method (fitted at run time from the case file's own `comparables`, so the
same code works on the practice cases and on the final case, whose
comparables also include the four practice launches):

  1. SHAPE. Every comparable past launch gives a release-day curve:
     viewers in UTC hours 0..23 of its release day, divided by its own
     hour-1 print.  In log space, ln(v_h / p1) is modelled per hour h as

         ln(v_h / p1) = a_h + b_h * x + c_h * console + noise,

     where x = ln(p1) - ln(P0) is the launch's (log) hour-1 size centred
     on P0, the weighted median hour-1 print of the comparables, and
     `console` is 1 for console-only launches (no PC in `platforms`),
     0 otherwise.  The regression is weighted least squares, console
     launches weighted 2x: they are the family this contest scores
     (GTA VI is PS5/Xbox, rolling midnight local; so are the practice
     launches), while PC/simultaneous launches still inform the size
     effect.  Three facts from the comparables drive the design:

       * The day has a shape: an overnight dip (median ~0.6-0.7x of
         hour 1 around hours 3-4), a jump at the US unlocks (hours
         5-6), then a climb to a peak of ~1.5-2.3x around hours 19-22
         (US prime time), easing into hour 24.
       * SIZE SATURATES.  log p1 predicts later growth with
         correlation about -0.71: a launch already at 850k in hour 1
         (PC simultaneous unlock, huge pre-release audience) barely
         grows (peak/p1 ~1.0-1.3), while a 20k hour-1 launch grows
         3-9x.  The fitted slopes b_h run about -0.2 in the morning
         to about -0.44 at the evening peak, clamped to [-0.6, 0.1].
       * The console dummy matters: console-only launches sit
         higher than the pooled shape at every daytime hour.

     x is clamped to [-2.0, +1.5] so a gigantic GTA VI hour-1 print
     (or a tiny one) extrapolates only that far; only comparables
     with hour-1 >= 10,000 viewers enter the fit (smaller ones are
     pre-launch noise / simultaneous-unlock artefacts with explosive,
     uninformative ratios).

  2. SPREAD. sigma_h is the weighted residual standard deviation of
     that hour's regression, smoothed over neighbouring hours and
     clamped to [0.25, 0.90] in log units.  Quantiles are log-normal:

         q_tau(h) = p1 * exp(a_h + b_h*x + c_h) * exp(z_tau * sigma_h)

     with z = (-1.645, -0.674, 0, 0.674, 1.645).  Spread multiplier 1.0:
     widening it to 1.15-1.5 helped only the single worst
     leave-one-out case and hurt the practice mean, so the honest
     residual spread is used as fitted.

  3. FALLBACK. If the case carries too few usable comparables (fewer
     than 5), or anything unexpected happens for a case, the program
     falls back to the house persistence baseline for that case
     (flat median, spread 0.15*sqrt(h-1)) rather than crashing: an
     invalid case scores as persistence anyway and forfeits prizes,
     so a valid fallback is strictly better. One level further out,
     a case file that cannot be read or forecast at all still gets a
     persistence output (best-effort hour-1 print, else 1.0), so the
     run never dies without writing that case's file.

Deterministic: no randomness, no network, no clock, no dict-order
dependence (comparables are processed in file order, sums in fixed
order); the same input bytes give the same output bytes.  Runs in
well under a second per case.

Validation (practice answers recovered from SullyGnome, the source
the kit cites; the kit ships no local grader and no answers):
mean pinball loss 0.203 vs persistence/starter 0.387 (-48%):
p00 Wolverine 0.308 (starter 0.744), p01 Red Dead 2 0.193 (0.287),
p02 God of War Ragnarok 0.141 (0.215), p03 Spider-Man 2 0.169
(0.303).  Leave-one-out over the 28 comparables with hour-1 >= 10k:
mean 0.267.  See RESULTS.md.
"""

import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path

# The grader's -P leaves /bot off sys.path: put it back first (harmless here,
# main.py is self-contained, but keeps the starter's convention).
sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.645, -0.674, 0.0, 0.674, 1.645]

# --- model constants (chosen on the comparables + practice cases; see
# module docstring. The loss is flat across the neighbouring settings:
# practice 0.203-0.206, leave-one-out 0.264-0.267, so these sit on a
# robust plateau, not a sharp peak) ---
MIN_P1 = 10000          # comparables with a smaller hour-1 print are excluded
CONSOLE_WEIGHT = 2.0    # weight of console-only launches in the fit
SLOPE_LO = -0.6         # clamp on the size slope b_h
SLOPE_HI = 0.1
X_LO, X_HI = -2.0, 1.5  # clamp on centred log hour-1 size of the case
SIGMA_LO, SIGMA_HI = 0.25, 0.90
FALLBACK_SIGMA = 0.15   # persistence fallback spread per sqrt-hour


def _parse_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def round2(x):
    """x rounded to 2 decimals, half up (as the starter does)."""
    scaled = x * 100
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 100
    return int(value) if value.is_integer() else value


def _wmedian(xs, ws):
    pairs = sorted(zip(xs, ws))
    total = sum(ws)
    acc = 0.0
    for x, w in pairs:
        acc += w
        if acc >= total / 2:
            return x
    return pairs[-1][0]


def _solve3(a_mat, b_vec):
    """Solve a 3x3 linear system by Gaussian elimination (deterministic)."""
    m = [row[:] + [b_vec[i]] for i, row in enumerate(a_mat)]
    for col in range(3):
        piv = max(range(col, 3), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        pv = m[col][col]
        if abs(pv) < 1e-12:
            m[col][col] = pv = 1e-12
        for r in range(3):
            if r != col:
                f = m[r][col] / pv
                for cc in range(col, 4):
                    m[r][cc] -= f * m[col][cc]
    return [m[i][3] / m[i][i] for i in range(3)]


def _comparable_curves(case):
    """Release-day hourly viewers per comparable, keyed by hour offset
    0..23 from its day_start, plus its console-only flag."""
    curves = []
    for comp in case.get("comparables") or []:
        try:
            day_start = _parse_time(comp["day_start"])
            vals = {}
            for row in comp.get("hourly") or []:
                off = int((_parse_time(row["from"]) - day_start).total_seconds() // 3600)
                if 0 <= off < 24 and row.get("viewers") is not None:
                    vals[off] = float(row["viewers"])
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        p1 = vals.get(0)
        if not p1 or p1 < MIN_P1:
            continue
        platforms = comp.get("platforms") or ""
        curves.append((vals, 1.0 if "PC" not in platforms else 0.0))
    return curves


def _fit_quantiles(p1, curves):
    """Per-hour (median log-ratio, sigma) from the weighted regression."""
    weighted = []
    for vals, console in curves:
        w = CONSOLE_WEIGHT if console else 1.0
        weighted.append((vals, console, w))
    p0 = _wmedian(
        [math.log(vals[0]) for vals, _, _ in weighted],
        [w for _, _, w in weighted],
    )
    x_case = max(X_LO, min(X_HI, math.log(max(1.0, p1)) - p0))
    per_hour = {}
    sigmas = {}
    for h in range(1, 24):
        rows = []
        for vals, console, w in weighted:
            if h in vals and vals[h] > 0:
                rows.append((math.log(vals[0]) - p0, console, math.log(vals[h] / vals[0]), w))
        if len(rows) < 5:
            raise ValueError("too few comparables for hour %d" % h)
        # Weighted least squares for y ~ a + b*x + c*console.
        xtx = [[0.0] * 3 for _ in range(3)]
        xty = [0.0] * 3
        for x, co, y, w in rows:
            feats = (1.0, x, co)
            for i in range(3):
                xty[i] += w * feats[i] * y
                for j in range(3):
                    xtx[i][j] += w * feats[i] * feats[j]
        _, b, c = _solve3(xtx, xty)
        b = max(SLOPE_LO, min(SLOPE_HI, b))
        sw = sum(w for _, _, _, w in rows)
        a = sum(w * (y - b * x - c * co) for x, co, y, w in rows) / sw
        resid = [y - (a + b * x + c * co) for x, co, y, _ in rows]
        var = sum(w * r * r for r, (_, _, _, w) in zip(resid, rows)) / sw
        sigmas[h] = math.sqrt(max(var, 0.0))
        # This competition's cases are console-only rolling-midnight
        # launches (GTA VI on PS5/Xbox; the practice launches likewise),
        # so the console dummy is 1 for the case being forecast.
        per_hour[h] = a + b * x_case + c * 1.0
    out = {}
    for h in range(1, 24):
        neighbours = [sigmas[hh] for hh in (h - 1, h, h + 1) if hh in sigmas]
        sigma = sum(neighbours) / len(neighbours)
        sigma = max(SIGMA_LO, min(SIGMA_HI, sigma))
        med = math.exp(per_hour[h])
        # Key by forecast hour (offset h is hour h + 1 of the day).
        out[h + 1] = [p1 * med * math.exp(z * sigma) for z in Z]
    return out


def _fallback_quantiles(p1, h):
    return [p1 * math.exp(z * FALLBACK_SIGMA * math.sqrt(h - 1)) for z in Z]


def forecast_case(case, name):
    if case.get("levels") != LEVELS:
        raise ValueError("%s asks for levels %s, not %s" % (name, case.get("levels"), LEVELS))
    first = next((k for k in case.get("known") or [] if k.get("hour") == 1), None)
    if first is None:
        raise ValueError("%s has no hour-1 print" % name)
    p1 = max(1.0, float(first["viewers"]))
    try:
        curves = _comparable_curves(case)
        if len(curves) < 5:
            raise ValueError("only %d usable comparables" % len(curves))
        quantiles = _fit_quantiles(p1, curves)
    except (ValueError, KeyError, TypeError, ZeroDivisionError, AttributeError):
        quantiles = {h: _fallback_quantiles(p1, h) for h in range(2, 25)}
    hours = []
    seen = set()
    for row in case["forecast"]:
        h = int(row["hour"])
        if h in seen:
            continue  # a duplicated forecast hour must not duplicate a row
        seen.add(h)
        hours.append(_clean_row(h, quantiles[h]))
    if {r["hour"] for r in hours} != set(range(2, 25)):
        # incomplete forecast list (e.g. empty): the emergency path
        # rebuilds the full 2..24 persistence forecast instead.
        raise ValueError("%s forecasts %d distinct hours, not 23" % (name, len(hours)))
    hours.sort(key=lambda r: r["hour"])
    return {"hours": hours}


def _clean_row(h, qs):
    """One output row: round half-up, clamp to (0, 1e9], force quantiles
    non-decreasing. Shared by the normal and emergency paths so every
    output row obeys the format rules."""
    out = []
    prev = 0.0
    for q in qs:
        v = round2(max(1.0, min(1e9, float(q))))
        v = max(v, prev)
        prev = v
        out.append(v)
    return {"hour": h, "q": out}


def _emergency_forecast(path):
    """Persistence forecast for a case that cannot be read or forecast at
    all: a valid output is strictly better than a crash, which the grader
    scores the same way (every quantile = hour-1 print) but leaves the
    output file missing. Best-effort hour-1 print, else 1.0."""
    p1 = 1.0
    hours = list(range(2, 25))
    try:
        case = json.loads(path.read_text(encoding="utf-8"))
        first = next((k for k in case.get("known") or [] if k.get("hour") == 1), None)
        if first is not None and first.get("viewers") is not None:
            p1 = max(1.0, float(first["viewers"]))
        if case.get("forecast"):
            hours = sorted({int(r["hour"]) for r in case["forecast"]
                            if 2 <= int(r["hour"]) <= 24} or list(range(2, 25)))
    except Exception:
        pass
    return {"hours": [_clean_row(h, _fallback_quantiles(p1, h)) for h in hours]}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        try:
            case = json.loads(path.read_text(encoding="utf-8"))
            forecast = forecast_case(case, path.name)
        except Exception:
            forecast = _emergency_forecast(path)
        text = json.dumps(forecast, separators=(",", ":")) + "\n"
        (OUTPUT_DIR / (path.stem + ".out")).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
