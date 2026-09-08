# Has Housing Rolled Over?

Residential construction is the earliest real-economy turn; this page keeps the score.
Live at [housing.chrislawrence.ca](https://housing.chrislawrence.ca).

No framework, no build step, no package manager. Plain HTML, CSS and vanilla JS on an
nginx front, with a stdlib-Python updater sidecar. Part of the economic tracker
collection (diesel, debt, jobs, yield) on the shared
[`econ-core`](https://github.com/Lawrence908/econ-core/blob/main/CONTRACT.md) series contract.

## Layout

```
src/index.html    markup, styling, the TimeChart canvas engine, every render function
data/series.json  machine-fetched, rewritten wholesale each run, never hand-edited
data/meta.json    curated; deliberately near-empty (housing has no hand-entered figure)
data/recessions.json  vendored from econ-core; never edited here
api/server.py     updater, rollover engine, and read-only status API
api/econcore.py   vendored, stamped copy of the shared fetchers
```

## The series

Twenty series ship in the payload on the econ-core contract. US: starts and permits,
total and single-family (1959/1960), new home sales and months' supply (1963), units
under construction (1970), Case-Shiller (1987) and FHFA (1975) prices with computed
year-over-year variants, the 30-year mortgage rate (1971), and the Leamer series,
residential investment as a share of GDP, quarterly since 1947. Canada: CMHC starts
monthly SAAR from 1990 and quarterly unadjusted from 1948 (vector v730416, still live;
the seasonally adjusted member v730427 is terminated at 2009 and deliberately unused),
single-detached from 1955, and the NHPI from 1981. Every StatCan vector is fetched with
its live English title verified first.

The deep Canadian line renders as a trailing four-quarter total: an annual sum is
seasonality-free by construction, no adjustment model involved, and it lands in the same
annualized units as the SAAR line, so their 1990-onward overlap shows the two
constructions agreeing.

## The rollover table, two clocks

Computed from single-family starts on every refresh, never hand-maintained. An episode
is a stretch of months with the 3-month average at least 20% below a year earlier
(three in a row to count; gaps under six months merge). Each episode is dated at its
CREST (where starts actually peaked, found in the two years before the alarm) and its
ALARM (the first month past the threshold), and an NBER peak from six months before the
alarm to 18 months after the last signal month is assigned to the nearest episode.

That two-clock design is the finding. On current data the crest led the seven credited
recessions by a median of 20 months (range 13 to 25), which is the four-to-six-quarter
lead the literature quotes; but the alarm fired before the recession in only three of
them. The turn is early, the confirmation is late. The known misses stay visible: 2001
(not housing-led; the yield curve caught it) and 2020 (too fast for a three-month gate)
appear in a computed uncredited list, 1966 and 2022-23 stand as false positives, and
the newest-row footnote renders entirely from the computed row so it cannot go stale.

The first build measured leads from the alarm alone and got a median of two months,
true and misleading at once; the rule was retuned once against the canonical narrative,
then frozen. It ships in the payload and prints beside the table.

## The updater

```bash
docker exec housing-updater python /app/server.py --once      # dry run
docker exec housing-updater python /app/server.py --refresh   # what cron runs
```

Host crontab, daily at 07:05 Pacific (releases scatter across the month, so most runs
verify rather than change), log bounded monthly. Guardrails: stale or shrunken upstreams
kept not written, failed fetches carry forward with the error recorded, revisions to
already-published observations logged to `changelog.jsonl`. Census starts revise
routinely, so that log is expected to be busy; the page says so.

Fetch policy is econ-core's: keyless first (FRED CSV, StatCan WDS), keyed FRED as
fallback (`FRED_API_KEY` in `.env`, gitignored).

## Provenance

Assembled with Claude, made by Anthropic. Measured series, one cited framing (Leamer,
"Housing IS the Business Cycle", Jackson Hole 2007), and computed history with the rule
printed. No forecasts, no valuation calls.
