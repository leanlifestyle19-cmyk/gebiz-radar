# GeBIZ Awards Radar — data pipeline

Tracks Singapore government procurement awards relevant to transformation /
BPR / change management work, using the MOF **Government Procurement via
GeBIZ** dataset on data.gov.sg (dataset `d_acde1106003906a75c3fa052592f2fcb`,
Open Data Licence — free for personal and commercial use).

**Awards intelligence only.** This dataset contains awarded outcomes
(supplier, amount, award date), not live open tenders, and refreshes roughly
monthly with ~1 month lag. Strategic signal, not bid alerts.

## Files

| Path | Purpose |
|---|---|
| `index.html` | The PWA — 4 tabs: New / Tenders / Agencies / Suppliers. Single file, vanilla JS. |
| `sw.js` | Service worker. Cache `gebizradar-20260715a` — **bump on every index.html deploy (M1)**. `awards.json` is network-first; app shell cache-first. |
| `manifest.json` | Install manifest (needs `icon-192.png`, `icon-512.png` in repo root). |
| `scripts/update_awards.py` | Fetch → filter → diff → write. Stdlib only, no pip installs. |
| `.github/workflows/update-awards.yml` | Weekly cron (Mon 8am SGT) + manual run button. Commits `data/awards.json` only when changed. |
| `data/awards.json` | Output the PWA fetches. Placeholder until first run. |
| `test/sample.csv` | Synthetic schema-matched CSV for local pipeline testing. |
| `test/awards-sample.json` | Pipeline output from the sample — copy over `data/awards.json` to preview the PWA with data before the first Action run. |

## Setup (from phone)

1. Create a new GitHub repo (e.g. `gebiz-radar`).
2. Add all files at the exact paths above (the workflow **must** live
   at `.github/workflows/update-awards.yml`).
3. Repo → Settings → Actions → General → Workflow permissions → set
   **Read and write permissions** (needed for the bot commit).
4. Repo → Settings → Pages → deploy from branch `main`, root folder.
5. Actions tab → "Update GeBIZ awards data" → **Run workflow** to trigger the
   first data run. Check that `data/awards.json` gets a commit.
6. Open `https://<user>.github.io/gebiz-radar/` on your phone, test, then
   Add to Home Screen. Test offline (M15: real device, real Pages URL).
7. Done — data self-updates every Monday morning; the app's ↻ Refresh (or
   any reload) picks it up because `awards.json` is fetched network-first.

**Every PWA deploy after this:** bump `CACHE` in `sw.js` (date form) and ship
`sw.js` + `index.html` together in the same commit (M1).

## How it decides what's relevant

`KEYWORDS` at the top of the script is a weighted dict. Each tender
description is scored by the sum of matched keyword weights (longest match
wins — "lean six sigma" doesn't also score "lean"). Tenders scoring
`>= MIN_SCORE` (currently 5) are kept. Tune both freely; the matched
keywords are stored per tender so you can audit why something appeared.

## Output shape

```json
{
  "meta":     { "lastUpdated", "sourceRows", "matchedTenders", "newThisRun", ... },
  "agencies": [ { "agency", "tenders", "totalAwarded" } ],   // sorted by spend
  "tenders":  [ { "tenderNo", "description", "agency", "awardDate",
                  "status", "score", "matched", "suppliers": [{"name","amount"}],
                  "totalAwarded", "firstSeen" } ]
}
```

`firstSeen` (SGT date) is stamped when a tender first appears and preserved
across runs — that's what powers a "New this week" view in the PWA.

## Data-source notes

- Primary fetch: `datastore_search` API with pagination (5,000 rows/page,
  1s pause between pages).
- Fallback: `poll-download` API → signed CSV URL → parse whole file
  (~4.2 MB, 18.5K rows). The script tries the API first and falls back
  automatically, so either endpoint changing shape won't break the run.
- Column headers differ between API (snake_case) and CSV (Title Case);
  both are handled. Award dates are `D/M/YYYY` in source, normalised to ISO.
- All "today" stamps use Asia/Singapore explicitly — never UTC (M6).

## Local test

```
python3 scripts/update_awards.py --local test/sample.csv
```