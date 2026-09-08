# housing.chrislawrence.ca — build plan

One narrow question: **has housing rolled over, and what has that meant for the cycle?**
Residential construction is the earliest real-economy turn: starts lead the cycle by four
to six quarters, and Leamer's Jackson Hole line ("housing IS the business cycle") is the
framing the page earns or refutes with its own table. Everything serves that question.

Site five of the family (diesel, debt, jobs, yield), third consumer of
`econ-core`. Written 2026-09-07; every series below was probed live that
day through econcore's own fetchers, so the depths are measured, not
assumed, and every StatCan vector was resolved from cube metadata and title-verified,
never guessed.

## Verified sources

### United States (FRED, keyless, all probed)

| Series | What | Depth | Freq | Latest |
|---|---|---|---|---|
| `HOUST` | Housing starts, total, SAAR | 1959-01 → 2026-07 | monthly | 1,239k |
| `HOUST1F` | Starts, single-family, SAAR | 1959-01 → | monthly | 808k |
| `PERMIT` | Building permits, total, SAAR | 1960-01 → | monthly | 1,433k |
| `PERMIT1` | Permits, single-family, SAAR | 1960-01 → | monthly | 894k |
| `HSN1F` | New single-family houses sold | 1963-01 → | monthly | 607k |
| `MSACSR` | Months' supply of new houses | 1963-01 → | monthly | 9.6 |
| `UNDCONTSA` | Units under construction, SAAR | 1970-01 → | monthly | 1,262k |
| `CSUSHPISA` | Case-Shiller national HPI, SA | 1987-01 → | monthly | 331.9 |
| `USSTHPI` | FHFA all-transactions HPI | 1975-Q1 → | quarterly | 719.9 |
| `MORTGAGE30US` | 30-year fixed mortgage rate | 1971-04 → | weekly | 6.71 |
| `A011RE1Q156NBEA` | Residential investment share of GDP | 1947-Q1 → | quarterly | 3.6% |

### Canada (StatCan WDS, keyless; vectors resolved and title-verified 2026-09-07)

| Vector | Table | What | Depth | Latest |
|---|---|---|---|---|
| `v730416` | 34-10-0135 | Starts, total, UNADJUSTED, quarterly, all areas | **1948-Q1 → 2026-Q2, live** | 66,788 units |
| `v730442` | 34-10-0135 | Starts, single-detached, unadjusted, quarterly | 1955-Q1 → live | 12,015 |
| `v52300157` | 34-10-0158 | Starts, total, SAAR, monthly, all areas | 1990-01 → | 229.1k |
| `v52299896/97` | 34-10-0156 | Starts total / single-detached, SAAR, monthly, centres 10k+ | 1990-01 → | 217.8k / 38.7k |
| `v111955442` | 18-10-0205 | New housing price index, Canada, total | 1981-01 → | 120.5 |

Findings from the probe that override assumptions:

1. **The SAAR quarterly vector in 34-10-0135 is a trap**: `v730427` is terminated (1966 →
   2009-Q4, dead). The cube's advertised 1948 depth lives in the UNADJUSTED members, which
   are live to 2026-Q2. Canada's deep history is therefore unadjusted actuals; see the
   construction rule below for how that renders honestly. Never wire v730427.
2. The user-cited monthly table 34-10-0158 is real but starts 1990. Single-detached
   monthly exists only in the centres-10k+ table (34-10-0156), a different coverage basis
   from all-areas; if drawn, that basis is stated.
3. No deep keyless Canadian mortgage-rate series surfaced (Valet's A4 lending-rate groups
   are recent-era regulatory data). Canada mortgage rate is out of v1; the US weekly series
   (1971 →) carries the affordability story, with a prose link to the yield site since the
   10-year drives it.
4. Residential investment share reads 3.6% of GDP against a 1947-2026 mean near 4.6%:
   housing enters this site's launch already depressed. The page states the comparison
   from computed tokens, not hardcoded numbers.

## Construction rules

- **Levels are drawn as published.** SAAR monthlies for the US and Canadian monthly
  starts; no smoothing baked into stored series (smoothing is a render option, stated).
- **Canada deep vs monthly cannot share an axis raw**: the monthly series is a seasonally
  adjusted annualized rate, the 1948 quarterly series is unadjusted actual units. The deep
  series renders as a **trailing 4-quarter sum**, which is an annual total by construction
  and therefore both seasonality-free and in annualized units, honestly comparable with
  the SAAR line where they overlap (1990 →). The construction is printed on the page;
  confidence `estimate`; raw quarterly obs ship alongside.
- **Prices render as year-over-year percent** on one chart: Case-Shiller SA (1987 →),
  FHFA quarterly (1975 →), Canada NHPI (1981 →). Same units, cross-country comparable,
  and YoY is where the "prices lag, volumes lead" story is visible. Index levels stay in
  the payload for anyone who wants them.
- **No secret seasonal adjustment, ever.** Anything computed here (YoY, trailing sums,
  drawdowns) is arithmetic on published series with the formula stated.

## The feature: the rollover table

The analogue of yield's episode table, computed on every refresh, rule shipped in the
payload and printed beside the rows. Candidate rule, to be tuned once against the
canonical record at build time and then frozen:

- Signal series: 3-month average of **single-family** starts (`HOUST1F`), the purest
  cyclical signal (multifamily is noisy and structurally driven). Compute the same table
  for total starts as a build-time check; ship the one that reads truer, say which, and
  why.
- An episode begins when the 3-month average falls at least 20% below its level a year
  earlier, sustained for 3 consecutive months; episodes separated by fewer than 6
  qualifying-free months merge. YoY framing self-terminates episodes and survives level
  shifts, unlike drawdown-from-peak, which never resolves after 2006.
- Scoring: an NBER peak inside [start − 6 months, end + 18 months] credits the episode.
  The lower bound exists for 2020, where the recession CAUSED the housing stop rather
  than the reverse; such a row renders as outcome "coincident", not as a lead.
- Expected canonical reading (verify computed table against this, then believe the
  table): busts before 1973-11, 1980-01, 1981-07, 1990-07, 2007-12 with long leads;
  1966-67 as the famous false positive (the credit crunch); **2001-03 absent entirely**,
  because the dot-com recession was not housing-led. That miss is content: yield caught
  2001 and housing did not, which is exactly what the eventual econ overlay is for.
  The 2022-23 rate-shock episode scores false-or-pending on current data.
- Derived stats: median lead among credited episodes, false-positive count, and the
  current drawdown of starts from their post-2020 peak for the status chip.

## Architecture

Clone the yield/jobs shape exactly; nothing here needs a new pattern.

- nginx front `housing` (host port **8149**, verified free in services.yml and listeners;
  the econ family then continues in the free 8132-8137 block) + stdlib sidecar
  `housing-updater` (internal 8000). One Caddy site file, one services.yml entry with
  dashy and kuma blocks (compact keyword `"status":"ok"`).
- Vendor econ-core (`./vendor.sh ../housing`): `api/econcore.py` +
  `data/recessions.json`. StatCan fetches use `wds_vector(v, expect_title=...)` with the
  titles verified above, so a renumbered vector fails loudly.
- `server.py` is yield's skeleton: FETCHED specs with provenance notes carrying the
  measured depths; derived series via `make_series` with `confidence: estimate`; analysis
  block `{status, episodes}` with the rule; jobs' guardrails (stale-upstream kept, >10%
  shrink kept, errors carry forward, revisions to changelog.jsonl). **Starts revise
  routinely** (unlike yields), so the revision card will actually be busy; the page says
  that instead of implying restatements are alarming.
- No curated projection exists for housing (there is no NY-Fed-equivalent published
  probability), so `data/meta.json` holds only `built` and any prose figure that must not
  live in markup. No monthly hand ritual. Nothing in this app is hand-refreshed.
- Host cron once daily at 07:05 PT (releases scatter: starts mid-month, permits later,
  Case-Shiller last Tuesday, FHFA quarterly, StatCan various, mortgage rate Thursdays;
  most runs verify rather than change), plus the monthly log truncation line.
- Vintages: reserved field only in v1, but housing is where ALFRED backfill becomes
  genuinely worth doing later, because starts revisions are material and "what did the
  rollover look like in real time in 2006" is the honest chart.

## Page

Same bones as yield (TimeChart engine, tokens, tiles, chip, no framework, no CDN, hub
footer linked): 

1. Header, the question, status chip: current starts vs their post-2020 peak, worded
   from computed drawdown ("Starts 1,239k SAAR, down N% from MMM YYYY"), amber when the
   rollover rule is currently signalling.
2. Tiles: total starts + drawdown, single-family starts, permits (the forward leg),
   residential investment share vs its 1947-2026 mean, months' supply.
3. Main chart: starts + permits SAAR, 1959/1960 →, NBER bands, single-family toggle,
   range presets.
4. The rollover table with its rule, the 1966 false positive, the 2001 miss stated in a
   footnote built from the computed rows (yield's pattern: claims about the newest row
   render from data so they cannot go stale).
5. Prices YoY: Case-Shiller + FHFA + NHPI, both countries' bands, "prices lag" caption.
6. The Leamer chart: residential investment share of GDP, 1947 →, bands; caption carries
   the citation and the computed current-vs-mean comparison.
7. Sales and supply duo: new home sales + months' supply (supply spikes precede busts;
   it is elevated at launch).
8. Mortgage rate, weekly 1971 →, with the cross-link to yield (the 10-year drives it).
9. Canada: monthly SAAR line + deep trailing-4-quarter line from 1948, C.D. Howe bands,
   construction and basis notes, optional single-detached quarterly from 1955.
10. Revisions card (expected busy; says so), sources card with every series, vector,
    verified depth and basis caveat, fetch policy, econ-core note, provenance line.

## Deploy checklist (identical to yield's, values changed)

Port 8149; `sites/housing.caddy` (`import proxied housing housing:80`); services.yml
entry after yield's with dashy + kuma blocks, re-parse; `cf-access.sh create
housing.chrislawrence.ca --policy public` + retry loop; cron lines + log truncation;
screenshots (390 fullPage, 1440, rollover-table card) + layout audit + console check;
`ls -l data/` chris-owned; commit; push private `Lawrence908/housing`.

## Anti-goals

- No hand-maintained derived data; the rollover table computes or it does not ship.
- No seasonal adjustment performed here, and no trailing-sum line without its
  construction printed.
- No splicing the 1948 quarterly Canadian series onto the 1990 monthly one; they overlap
  and the overlap is shown.
- No affordability index, no valuation model, no "overvalued by X%" claims: measured
  series, published projections by named institutions (there are none for housing), and
  computed history with the rule printed. Nothing else.
- No Teranet or CREA MLS HPI: not keyless-redistributable. NHPI carries Canada prices
  with its new-construction-only basis stated.
- No emdashes in page copy.

## Acceptance

- All fifteen-ish series land with zero errors on a cold start; contract-validated.
- The rollover table reproduces the canonical narrative (leads into 1973/1980/1981/1990/
  2008, 1966 false positive, 2001 absent, 2020 coincident, 2022-23 unresolved) or the
  discrepancy is investigated until the table is believed over the narrative.
- Canada deep line matches the SAAR line within seasonal-construction tolerance over
  their 1990-2026 overlap, visible on the page.
- Kill `FRED_API_KEY`: everything still refreshes (keyless CSV + WDS are primary).
- Both containers healthy, public 200, Kuma green, screenshots committed, zero console
  errors, no horizontal scroll, repo pushed, no machine-owned files in git.
