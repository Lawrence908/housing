#!/usr/bin/env python3
"""housing.chrislawrence.ca data updater and read-only status API.

One narrow question: has housing rolled over, and what has that meant for the
cycle? Starts lead the cycle by four to six quarters; this serves the series
and the computed rollover table that keep that claim honest.

Everything live on the page comes from series.json, machine-owned and
rewritten wholesale each run. data/meta.json and the vendored recessions.json
are never touched by automation. Unlike yield, nothing here is hand-refreshed:
housing has no NY-Fed-equivalent published probability, so there is no curated
figure and no monthly ritual.

Guardrails, inherited from jobs: stale or shrunken upstreams are kept rather
than written, a failed fetch carries the previous series forward and records
the error, and revisions to already-published observations land in
changelog.jsonl. Census starts and permits revise routinely (each month
restates the prior two), so the revision log here is expected to be busy;
that is the data behaving normally, not an alarm.

The rollover table is recomputed from shipped inputs on every run and never
hand-maintained. The detection rule ships in the payload so the page prints
the rule that produced the table.

HTTP here is read-only. Runs happen via host cron calling
`docker exec housing-updater python /app/server.py --refresh`.
"""

import json
import os
import sys
import threading
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import econcore

FRED_KEY = os.environ.get("FRED_API_KEY", "").strip()
DATA_DIR = os.environ.get("DATA_DIR", "/app/data")

SERIES_FILE = os.path.join(DATA_DIR, "series.json")
CHANGELOG = os.path.join(DATA_DIR, "changelog.jsonl")
STATE_FILE = os.path.join(DATA_DIR, "updater-state.json")
RECESSIONS_FILE = os.path.join(DATA_DIR, "recessions.json")

CURATED = ["meta", "recessions"]
SHRINK_TOLERANCE = 0.9
CHANGELOG_IN_PAYLOAD = 100

# The rollover rule. Ships in the payload so the page states the rule that
# produced the table; a different rule gives a different table. Thresholds
# were tuned once against the canonical narrative at build time (see
# BUILD-PLAN.md) and are frozen; they are content, not knobs.
EPISODE_RULE = {
    "series": "us_starts_1f",
    "basis": "3-month average, percent change from a year earlier",
    "threshold_yoy_pct": -20.0,
    "sustain_months": 3,
    "merge_gap_months": 6,
    "window_before_months": 6,
    "window_after_months": 18,
    "crest_lookback_months": 24,
    "statement": ("A rollover episode is a stretch of months with the 3-month "
                  "average of single-family starts at least 20% below its "
                  "level a year earlier, containing at least three such "
                  "months in a row; stretches separated by fewer than six "
                  "clear months merge into one. Each episode is dated two "
                  "ways: the CREST, where the 3-month average peaked in the "
                  "two years before the alarm (the actual rollover), and the "
                  "ALARM, the first month past the threshold. An NBER peak "
                  "within six months before the alarm through 18 months "
                  "after the last signal month is assigned to the nearest "
                  "episode; lead time runs from the crest, and the outcome "
                  "says whether the alarm fired before or after the "
                  "recession began, because a 20% year-on-year decline is a "
                  "late confirmer while the crest is the early turn."),
}

_payload_cache = {"stamp": None, "body": None}
_state = {"last_run": None, "results": []}
_lock = threading.Lock()


# --------------------------------------------------------------------------
# the series list
#
# Adding a series is a human decision with a verified source; the updater
# only refreshes what is declared. Depths in the notes were probed live on
# 2026-09-07, not assumed. StatCan vectors were resolved from cube metadata
# and carry expect_title so a renumbered vector fails loudly instead of
# charting someone else's numbers.
# --------------------------------------------------------------------------

def _fred(series_id):
    return lambda: econcore.fred_series(series_id, FRED_KEY)


def _wds(vector_id, expect_title):
    return lambda: econcore.wds_vector(vector_id, expect_title=expect_title)


STARTS_TABLE = "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3410013501"
STARTS_SAAR_TABLE = "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3410015801"
NHPI_TABLE = "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1810020501"

FETCHED = [
    {
        "id": "us_starts",
        "fetch": _fred("HOUST"),
        "label": "US housing starts",
        "source": "US Census Bureau and HUD new residential construction, via FRED HOUST",
        "source_url": "https://fred.stlouisfed.org/series/HOUST",
        "units": "thousands_of_units_annualized", "freq": "monthly",
        "note": "Total privately-owned housing units started, seasonally adjusted annual rate, monthly since January 1959. The earliest real-economy turn: starts lead the cycle by four to six quarters.",
    },
    {
        "id": "us_starts_1f",
        "fetch": _fred("HOUST1F"),
        "label": "US single-family starts",
        "source": "US Census Bureau and HUD, via FRED HOUST1F",
        "source_url": "https://fred.stlouisfed.org/series/HOUST1F",
        "units": "thousands_of_units_annualized", "freq": "monthly",
        "note": "Single-family units only, SAAR, since January 1959. The purest cyclical signal; multifamily is noisy and structurally driven. The rollover table computes from this series.",
    },
    {
        "id": "us_permits",
        "fetch": _fred("PERMIT"),
        "label": "US building permits",
        "source": "US Census Bureau building permits survey, via FRED PERMIT",
        "source_url": "https://fred.stlouisfed.org/series/PERMIT",
        "units": "thousands_of_units_annualized", "freq": "monthly",
        "note": "Units authorized by building permits, SAAR, since January 1960. Permits precede starts; the forward leg of the pair.",
    },
    {
        "id": "us_permits_1f",
        "fetch": _fred("PERMIT1"),
        "label": "US single-family permits",
        "source": "US Census Bureau, via FRED PERMIT1",
        "source_url": "https://fred.stlouisfed.org/series/PERMIT1",
        "units": "thousands_of_units_annualized", "freq": "monthly",
        "note": "Single-family units authorized, SAAR, since January 1960.",
    },
    {
        "id": "us_new_home_sales",
        "fetch": _fred("HSN1F"),
        "label": "US new home sales",
        "source": "US Census Bureau new residential sales, via FRED HSN1F",
        "source_url": "https://fred.stlouisfed.org/series/HSN1F",
        "units": "thousands_of_units_annualized", "freq": "monthly",
        "note": "New single-family houses sold, SAAR, since January 1963.",
    },
    {
        "id": "us_months_supply",
        "fetch": _fred("MSACSR"),
        "label": "US months' supply of new houses",
        "source": "US Census Bureau, via FRED MSACSR",
        "source_url": "https://fred.stlouisfed.org/series/MSACSR",
        "units": "months", "freq": "monthly",
        "note": "New houses for sale relative to the sales pace, since January 1963. Supply spikes have preceded every housing bust in the sample.",
    },
    {
        "id": "us_under_construction",
        "fetch": _fred("UNDCONTSA"),
        "label": "US units under construction",
        "source": "US Census Bureau, via FRED UNDCONTSA",
        "source_url": "https://fred.stlouisfed.org/series/UNDCONTSA",
        "units": "thousands_of_units", "freq": "monthly",
        "note": "The stock of units under construction, seasonally adjusted, since 1970. A backlog measure: starts can fall while this stays high and employment holds up, which is exactly the 2023-24 story.",
    },
    {
        "id": "us_hpi_cs",
        "fetch": _fred("CSUSHPISA"),
        "label": "Case-Shiller national home price index",
        "source": "S&P CoreLogic Case-Shiller US National, seasonally adjusted, via FRED CSUSHPISA",
        "source_url": "https://fred.stlouisfed.org/series/CSUSHPISA",
        "units": "index_jan2000_100", "freq": "monthly",
        "note": "Repeat-sales index, monthly since January 1987, published with a two-month lag. Prices lag volumes; that ordering is the point of this page.",
    },
    {
        "id": "us_hpi_fhfa",
        "fetch": _fred("USSTHPI"),
        "label": "FHFA all-transactions house price index",
        "source": "Federal Housing Finance Agency, via FRED USSTHPI",
        "source_url": "https://fred.stlouisfed.org/series/USSTHPI",
        "units": "index_1980q1_100", "freq": "quarterly",
        "note": "Quarterly since 1975, twelve years deeper than Case-Shiller. Based on conforming-mortgage transactions, so it under-weights the top of the market.",
    },
    {
        "id": "us_mortgage_30y",
        "fetch": _fred("MORTGAGE30US"),
        "label": "US 30-year fixed mortgage rate",
        "source": "Freddie Mac Primary Mortgage Market Survey, via FRED MORTGAGE30US",
        "source_url": "https://fred.stlouisfed.org/series/MORTGAGE30US",
        "units": "percent", "freq": "weekly",
        "note": "Weekly since April 1971. The affordability lever, and it rides the 10-year Treasury; the yield tracker at yield.chrislawrence.ca is the upstream story.",
    },
    {
        "id": "us_resid_invest_share",
        "fetch": _fred("A011RE1Q156NBEA"),
        "label": "US residential investment share of GDP",
        "source": "Bureau of Economic Analysis NIPA, via FRED A011RE1Q156NBEA",
        "source_url": "https://fred.stlouisfed.org/series/A011RE1Q156NBEA",
        "units": "percent_of_gdp", "freq": "quarterly",
        "note": "Quarterly since 1947. Leamer's measure: housing's weight in the economy peaks before recessions and troughs after them.",
    },
    {
        "id": "ca_starts_saar",
        "fetch": _wds(52300157, "Canada"),
        "label": "Canada housing starts, monthly",
        "source": "CMHC via Statistics Canada table 34-10-0158-01, vector v52300157",
        "source_url": STARTS_SAAR_TABLE,
        "units": "thousands_of_units_annualized", "freq": "monthly",
        "note": "All areas, total units, seasonally adjusted at annual rates, monthly since January 1990. That start date is the table's depth, not the market's; the quarterly series below carries the deep history.",
    },
    {
        "id": "ca_starts_q",
        "fetch": _wds(730416, "Housing starts"),
        "label": "Canada housing starts, quarterly, unadjusted",
        "source": "CMHC via Statistics Canada table 34-10-0135-01, vector v730416",
        "source_url": STARTS_TABLE,
        "units": "units_per_quarter", "freq": "quarterly",
        "note": "Actual units started per quarter, all areas, UNADJUSTED, since 1948-Q1 and still live. The seasonally-adjusted member of this table (v730427) is terminated at 2009 and is deliberately not used. Seasonality is removed downstream by a stated trailing-4-quarter sum, never silently.",
    },
    {
        "id": "ca_starts_1f_q",
        "fetch": _wds(730442, "Single-detached"),
        "label": "Canada single-detached starts, quarterly, unadjusted",
        "source": "CMHC via Statistics Canada table 34-10-0135-01, vector v730442",
        "source_url": STARTS_TABLE,
        "units": "units_per_quarter", "freq": "quarterly",
        "note": "Single-detached units, unadjusted, quarterly since 1955.",
    },
    {
        "id": "ca_nhpi",
        "fetch": _wds(111955442, "Total (house and land)"),
        "label": "Canada new housing price index",
        "source": "Statistics Canada table 18-10-0205-01, vector v111955442",
        "source_url": NHPI_TABLE,
        "units": "index_201612_100", "freq": "monthly",
        "note": "Contractors' selling prices of new houses, total (house and land), monthly since 1981. New construction only: it misses the resale market, which is stated wherever it is drawn. Teranet and CREA indexes are not keyless-redistributable.",
    },
]


# --------------------------------------------------------------------------
# derived series: the constructions, stated
# --------------------------------------------------------------------------

def _trailing_4q_sum(obs, scale=1.0):
    """[[date, sum of this and prior 3 quarters], ...]; an annual total by
    construction, therefore seasonality-free without any adjustment model."""
    out = []
    for i in range(3, len(obs)):
        window = obs[i - 3:i + 1]
        out.append([obs[i][0], round(sum(v for _, v in window) * scale, 3)])
    return out


def build_derived(series):
    """Computed from shipped inputs on every run. Confidence is 'estimate'
    because the arithmetic happens here; every input is a reported series in
    the same payload."""
    out = {}

    for src_id, new_id, periods, label in [
        ("us_hpi_cs", "us_hpi_cs_yoy", 12, "Case-Shiller national, year over year"),
        ("us_hpi_fhfa", "us_hpi_fhfa_yoy", 4, "FHFA all-transactions, year over year"),
        ("ca_nhpi", "ca_nhpi_yoy", 12, "Canada NHPI, year over year"),
    ]:
        src = series.get(src_id)
        if not src:
            continue
        out[new_id] = econcore.make_series(
            new_id, label,
            "Derived: 12-month percent change of " + src["source"],
            src["source_url"], "percent", src["freq"],
            [[d, round(v, 2)] for d, v in
             econcore.yoy_percent(src["obs"], periods)],
            confidence="estimate",
            note="Computed here from the index level series in this payload.")

    caq = series.get("ca_starts_q")
    if caq:
        out["ca_starts_trailing4q"] = econcore.make_series(
            "ca_starts_trailing4q",
            "Canada housing starts, trailing four quarters",
            "Derived: trailing 4-quarter sum of StatCan v730416 (unadjusted quarterly starts)",
            STARTS_TABLE, "thousands_of_units_annualized", "quarterly",
            _trailing_4q_sum(caq["obs"], scale=1e-3),
            confidence="estimate",
            note="A trailing annual total, seasonality-free by construction rather than by model, in the same annualized units as the monthly SAAR series so the two are honestly comparable over their 1990-onward overlap. Raw quarterly observations ship alongside.")

    ca1f = series.get("ca_starts_1f_q")
    if ca1f:
        out["ca_starts_1f_trailing4q"] = econcore.make_series(
            "ca_starts_1f_trailing4q",
            "Canada single-detached starts, trailing four quarters",
            "Derived: trailing 4-quarter sum of StatCan v730442",
            STARTS_TABLE, "thousands_of_units_annualized", "quarterly",
            _trailing_4q_sum(ca1f["obs"], scale=1e-3),
            confidence="estimate",
            note="Same construction as the total series; single-detached only, from 1955.")

    return out


# --------------------------------------------------------------------------
# analysis: status and the rollover table
# --------------------------------------------------------------------------

def _mi(year_month):
    year, month = year_month.split("-")[:2]
    return int(year) * 12 + int(month) - 1


def _three_month_avg(obs):
    return [[obs[i][0], (obs[i][1] + obs[i - 1][1] + obs[i - 2][1]) / 3.0]
            for i in range(2, len(obs))]


def _yoy(avgs, periods=12):
    out = []
    for i in range(periods, len(avgs)):
        prev = avgs[i - periods][1]
        if prev:
            out.append([avgs[i][0], (avgs[i][1] / prev - 1.0) * 100.0])
    return out


def build_status(series):
    """The chip and tiles: where starts sit against their recent peak, and
    whether the rollover rule is signalling right now."""
    status = {}
    for sid in ("us_starts", "us_starts_1f"):
        entry = series.get(sid)
        if not entry:
            continue
        avgs = _three_month_avg(entry["obs"])
        if len(avgs) < 61:
            continue
        window = avgs[-60:]
        peak = max(window, key=lambda a: a[1])
        latest = avgs[-1]
        yoy = _yoy(avgs)
        status[sid] = {
            "latest": [entry["obs"][-1][0], entry["obs"][-1][1]],
            "latest_3mma": [latest[0], round(latest[1], 1)],
            "peak_3mma_5y": [peak[0], round(peak[1], 1)],
            "drawdown_pct": round((latest[1] / peak[1] - 1.0) * 100.0, 1),
            "yoy_3mma_pct": round(yoy[-1][1], 1) if yoy else None,
        }
    sf = status.get("us_starts_1f")
    if sf and sf["yoy_3mma_pct"] is not None:
        status["signal_active"] = sf["yoy_3mma_pct"] <= EPISODE_RULE["threshold_yoy_pct"]
    share = series.get("us_resid_invest_share")
    if share:
        values = [v for _, v in share["obs"]]
        status["resid_share"] = {
            "latest": [share["obs"][-1][0], share["obs"][-1][1]],
            "mean_since_1947": round(sum(values) / len(values), 2),
        }
    return status


def build_episodes(sf_entry, recessions):
    """The rollover table. A different rule gives a different table, so the
    rule ships alongside the rows.

    Two clocks per episode, deliberately: the CREST (where the 3-month
    average peaked before the decline) is where the canonical "starts lead
    by 4-6 quarters" is measured from, while the ALARM (first month past the
    YoY threshold) is what was knowable in real time. The first build ran
    with alarm-only leads and got a median of 2 months, which is true and
    misleading at once; showing both clocks is the honest resolution."""
    avgs = _three_month_avg(sf_entry["obs"])
    level = {d[:7]: v for d, v in avgs}
    yoy = _yoy(avgs)
    months = [[d[:7], v] for d, v in yoy]
    threshold = EPISODE_RULE["threshold_yoy_pct"]
    qualifying = [i for i, (_, v) in enumerate(months) if v <= threshold]
    if not qualifying:
        return {"rule": EPISODE_RULE, "episodes": [], "stats": {}}

    # consecutive runs, then merge runs separated by fewer clear months than
    # the rule allows, then keep merged groups containing a sustained run
    runs = [[qualifying[0], qualifying[0]]]
    for i in qualifying[1:]:
        if i == runs[-1][1] + 1:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    groups = [runs[0][:]]
    for start_i, end_i in runs[1:]:
        gap = _mi(months[start_i][0]) - _mi(months[groups[-1][1]][0]) - 1
        if gap < EPISODE_RULE["merge_gap_months"]:
            groups[-1][1] = end_i
        else:
            groups.append([start_i, end_i])

    def longest_run(lo, hi):
        best = run = 0
        for i in range(lo, hi + 1):
            run = run + 1 if months[i][1] <= threshold else 0
            best = max(best, run)
        return best

    groups = [g for g in groups
              if longest_run(g[0], g[1]) >= EPISODE_RULE["sustain_months"]]

    # assemble episode shells first, so peak assignment can prefer the
    # NEAREST episode: without that, a long window lets the 1979-80 episode
    # swallow the 1981-07 peak and the genuine 1981-82 collapse scores as a
    # false positive (caught on the first dry run).
    def month_at(mi_value):
        return "%04d-%02d" % (mi_value // 12, mi_value % 12 + 1)

    shells = []
    for lo, hi in groups:
        span = months[lo:hi + 1]
        start, end = span[0][0], span[-1][0]
        look = [month_at(m) for m in
                range(_mi(start) - EPISODE_RULE["crest_lookback_months"],
                      _mi(start) + 1)]
        crest = max((m for m in look if m in level), key=lambda m: level[m])
        after = [month_at(m) for m in range(_mi(start), _mi(end) + 7)]
        bottom = min((m for m in after if m in level), key=lambda m: level[m])
        shells.append({
            "start": start, "end": end, "span": span,
            "crest": crest, "bottom": bottom,
            "window_lo": _mi(start) - EPISODE_RULE["window_before_months"],
            "window_hi": _mi(end) + EPISODE_RULE["window_after_months"],
            "peaks": [],
        })

    bands = recessions["us"]["bands"]
    data_through = _mi(recessions["us"]["as_of"][:7])
    assigned = set()
    for band in bands:
        peak = band["peak"]
        candidates = [s for s in shells
                      if s["window_lo"] <= _mi(peak) <= s["window_hi"]]
        if not candidates:
            continue
        best = max(candidates, key=lambda s: _mi(s["start"]))
        best["peaks"].append(peak)
        assigned.add(peak)

    episodes = []
    for s in shells:
        led = [p for p in s["peaks"] if _mi(p) >= _mi(s["start"])]
        if led:
            outcome = "recession"
        elif s["peaks"]:
            outcome = "coincident"
        elif s["window_hi"] > data_through:
            outcome = "pending"
        else:
            outcome = "none_in_window"
        first_peak = s["peaks"][0] if s["peaks"] else None
        episodes.append({
            "start": s["start"],
            "end": s["end"],
            "crest": {"month": s["crest"],
                      "value": round(level[s["crest"]], 1)},
            "bottom": {"month": s["bottom"],
                       "value": round(level[s["bottom"]], 1)},
            "depth_pct": round((level[s["bottom"]] / level[s["crest"]] - 1.0)
                               * 100.0, 1),
            "months_signalling": len([1 for _, v in s["span"]
                                      if v <= threshold]),
            "recessions": s["peaks"],
            "lead_from_crest_months": (_mi(first_peak) - _mi(s["crest"])
                                       if first_peak else None),
            "lead_from_alarm_months": (_mi(first_peak) - _mi(s["start"])
                                       if first_peak else None),
            "outcome": outcome,
        })

    leads = sorted(e["lead_from_crest_months"] for e in episodes
                   if e["recessions"])
    stats = {}
    if leads:
        mid = len(leads) // 2
        median = (leads[mid] if len(leads) % 2
                  else (leads[mid - 1] + leads[mid]) / 2.0)
        stats = {"credited_episodes": len(leads),
                 "median_lead_from_crest_months": median,
                 "min_lead_from_crest_months": leads[0],
                 "max_lead_from_crest_months": leads[-1],
                 "alarm_led": len([e for e in episodes
                                   if e["outcome"] == "recession"]),
                 "coincident": len([e for e in episodes
                                    if e["outcome"] == "coincident"]),
                 "false_positives": len([e for e in episodes
                                         if e["outcome"] == "none_in_window"]),
                 "pending": len([e for e in episodes
                                 if e["outcome"] == "pending"]),
                 "uncredited_recessions": [
                     b["peak"] for b in bands
                     if _mi(b["peak"]) >= _mi(months[0][0])
                     and b["peak"] not in assigned]}
    return {"rule": EPISODE_RULE, "episodes": episodes, "stats": stats}


def build_analysis(series):
    analysis = {"status": build_status(series)}
    sf = series.get("us_starts_1f")
    if sf:
        try:
            recessions = econcore.load_recessions(RECESSIONS_FILE)
            analysis["episodes"] = build_episodes(sf, recessions)
        except Exception as exc:  # noqa: BLE001 - the table degrades, the page renders
            analysis["episodes_error"] = "%s: %s" % (type(exc).__name__, exc)
    return analysis


# --------------------------------------------------------------------------
# refresh
# --------------------------------------------------------------------------

def load_old_series():
    try:
        with open(SERIES_FILE) as fh:
            return json.load(fh).get("series", {})
    except Exception:  # noqa: BLE001 - first run, or corrupt file: start clean
        return {}


def _diff_revisions(series_id, old_obs, new_obs):
    """Changed values at already-published dates, raw fetched series only.
    Derived series move when an input moves; diffing them too would report
    the same restatement several times (diesel's rule)."""
    old_map = dict(map(tuple, old_obs))
    changed = [(d, old_map[d], v) for d, v in new_obs
               if d in old_map and abs(old_map[d] - v) > 1e-9]
    if not changed:
        return None
    deltas = [abs(after - before) for _, before, after in changed]
    return {
        "series": series_id, "action": "revised",
        "changed": len(changed),
        "span": [changed[0][0], changed[-1][0]],
        "max_delta": round(max(deltas), 4),
        "sample": [{"date": d, "before": b, "after": a}
                   for d, b, a in changed[:3]],
    }


def refresh_series(dry=False):
    old = load_old_series()
    series, errors, results = {}, {}, []

    for spec in FETCHED:
        sid = spec["id"]
        prev = old.get(sid)
        rec = {"series": sid, "action": "fetched"}
        try:
            obs = spec["fetch"]()
            doc = econcore.make_series(
                sid, spec["label"], spec["source"], spec["source_url"],
                spec["units"], spec["freq"], obs, note=spec.get("note"))
            if prev and prev.get("obs"):
                if doc["as_of"] < prev["as_of"]:
                    rec.update(action="stale-upstream",
                               reason="upstream at %s, behind stored %s; kept"
                                      % (doc["as_of"], prev["as_of"]))
                    doc = prev
                elif len(obs) < len(prev["obs"]) * SHRINK_TOLERANCE:
                    rec.update(action="shrunk",
                               reason="%d obs against %d stored; kept"
                                      % (len(obs), len(prev["obs"])))
                    doc = prev
                else:
                    revision = _diff_revisions(sid, prev["obs"], obs)
                    if revision and prev.get("source") == doc.get("source"):
                        if not dry:
                            econcore.log_revision(CHANGELOG, revision)
                        rec.update(action="revised",
                                   changed=revision["changed"])
                    added = len(obs) - len(prev["obs"])
                    if added > 0:
                        rec["added"] = added
            series[sid] = doc
        except Exception as exc:  # noqa: BLE001 - one dead endpoint, one chart
            errors[sid] = "%s: %s" % (type(exc).__name__, exc)
            rec.update(action="error", reason=errors[sid])
            if prev:
                series[sid] = prev
                rec["carried_forward"] = True
        results.append(rec)
        print("%-28s %-14s %s" % (sid, rec["action"], rec.get("reason", "")),
              flush=True)

    if not series:
        raise ValueError("nothing fetched and nothing stored; refusing to write")

    series.update(build_derived(series))
    analysis = build_analysis(series)

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "note": "Machine-fetched. Never hand-edited; the updater rewrites this file wholesale.",
        "econcore": econcore.VERSION,
        "fred_key_used": bool(FRED_KEY),
        "errors": errors,
        "series": series,
        "analysis": analysis,
    }

    if dry:
        total = sum(len(s["obs"]) for s in series.values())
        print("dry run: %d series, %d observations, %d errors -- not written"
              % (len(series), total, len(errors)), flush=True)
        return payload

    tmp = SERIES_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    os.chmod(tmp, 0o644)
    os.replace(tmp, SERIES_FILE)

    with _lock:
        _state["last_run"] = datetime.now(timezone.utc).isoformat()
        _state["results"] = results
    _save_state()

    total = sum(len(s["obs"]) for s in series.values())
    print("series refreshed: %d series, %d observations, %d errors"
          % (len(series), total, len(errors)), flush=True)
    return payload


def _save_state():
    try:
        with _lock:
            snapshot = dict(_state)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(snapshot, fh, indent=2)
        os.chmod(tmp, 0o644)
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass


# --------------------------------------------------------------------------
# read-only HTTP
# --------------------------------------------------------------------------

def _load(name):
    with open(os.path.join(DATA_DIR, name)) as fh:
        return json.load(fh)


def data_stamp():
    newest = 0.0
    names = [n + ".json" for n in CURATED] + ["series.json", "changelog.jsonl"]
    for name in names:
        try:
            newest = max(newest, os.path.getmtime(os.path.join(DATA_DIR, name)))
        except OSError:
            continue
    return newest


def build_data_payload():
    """Composed from disk, cached on mtime: the refresh runs outside this
    process via docker exec, so an in-memory payload would keep serving
    superseded figures behind a healthy endpoint."""
    stamp = data_stamp()
    if _payload_cache["stamp"] == stamp and _payload_cache["body"] is not None:
        return _payload_cache["body"]

    payload = {"generated_at": datetime.now(timezone.utc).isoformat()}
    for name in CURATED:
        try:
            payload[name] = _load(name + ".json")
        except Exception as exc:  # noqa: BLE001 - reported, not fatal
            payload[name] = None
            payload.setdefault("errors", {})[name] = str(exc)
    try:
        doc = _load("series.json")
        payload["series"] = doc.get("series", {})
        payload["analysis"] = doc.get("analysis", {})
        payload["series_fetched_at"] = doc.get("fetched_at")
        payload["series_errors"] = doc.get("errors", {})
    except Exception as exc:  # noqa: BLE001 - charts degrade, page renders
        payload["series"] = {}
        payload["analysis"] = {}
        payload.setdefault("errors", {})["series"] = str(exc)

    recent, total = econcore.read_revisions(CHANGELOG, CHANGELOG_IN_PAYLOAD)
    payload["changelog"] = {"total": total, "recent": recent}

    _payload_cache["stamp"] = stamp
    _payload_cache["body"] = payload
    return payload


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, cache="no-cache"):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/health":
            # Probe the dependency, not the process: no data, not healthy.
            try:
                doc = _load("series.json")
                starts = doc.get("series", {}).get("us_starts", {})
                st = doc.get("analysis", {}).get("status", {})
                self._send(200, {
                    "status": "ok",
                    "series": len(doc.get("series", {})),
                    "latest": starts.get("as_of"),
                    "signal_active": st.get("signal_active"),
                    "errors": len(doc.get("errors", {})),
                    "fetched_at": doc.get("fetched_at"),
                })
            except Exception as exc:  # noqa: BLE001 - absent data IS the unhealthy case
                self._send(503, {"status": "no data", "error": str(exc)})
        elif path == "/api/data":
            self._send(200, build_data_payload(),
                       cache="public, max-age=300, must-revalidate")
        elif path == "/api/status":
            with _lock:
                snapshot = dict(_state)
            snapshot["fred_key"] = bool(FRED_KEY)
            snapshot["econcore"] = econcore.VERSION
            self._send(200, snapshot)
        elif path == "/api/changelog":
            recent, total = econcore.read_revisions(CHANGELOG, CHANGELOG_IN_PAYLOAD)
            self._send(200, {"total": total, "recent": recent})
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        return


def main():
    if "--refresh" in sys.argv:
        refresh_series()
        return
    if "--once" in sys.argv:
        refresh_series(dry=True)
        return

    print("updater starting: fred_key=%s (schedule: host cron)"
          % bool(FRED_KEY), flush=True)

    def warm():
        try:
            refresh_series()
        except Exception as exc:  # noqa: BLE001 - server must come up regardless
            print("initial fetch failed: %s" % exc, flush=True)

    threading.Thread(target=warm, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()


if __name__ == "__main__":
    main()
