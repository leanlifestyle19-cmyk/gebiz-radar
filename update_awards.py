#!/usr/bin/env python3
"""
GeBIZ Awards Radar — data pipeline.

Fetches the MOF "Government Procurement via GeBIZ" dataset from data.gov.sg,
filters rows against transformation/BPR keywords, diffs against the previous
run, and writes data/awards.json for the PWA to consume.

Stdlib only — no pip installs needed in the Action runner.

Usage:
  python3 scripts/update_awards.py                 # normal run (fetch from data.gov.sg)
  python3 scripts/update_awards.py --local FILE    # test run against a local CSV
"""

import csv
import io
import json
import re
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATASET_ID = "d_acde1106003906a75c3fa052592f2fcb"  # Government Procurement via GeBIZ (MOF)

DATASTORE_URL = "https://data.gov.sg/api/action/datastore_search"
POLL_DOWNLOAD_URL = f"https://api-open.data.gov.sg/v1/public/api/datasets/{DATASET_ID}/poll-download"

OUTPUT_PATH = "data/awards.json"

# Weighted keywords. Score = sum of weights for each keyword found in the
# tender description (case-insensitive). Tune freely — higher weight = more
# central to your niche. A row is kept if score >= MIN_SCORE.
KEYWORDS = {
    # Core niche
    "business process reengineering": 10,
    "business process re-engineering": 10,
    "process reengineering": 9,
    "organisational transformation": 10,
    "organizational transformation": 10,
    "change management": 8,
    "transformation": 6,
    "process improvement": 7,
    "process redesign": 8,
    "service redesign": 7,
    "lean six sigma": 9,
    "lean": 4,
    "six sigma": 6,
    "capability development": 7,
    "operational excellence": 7,
    "ops review": 6,
    "operations review": 6,
    "organisation review": 6,
    "organization review": 6,
    "manpower study": 5,
    "workforce transformation": 7,
    # Adjacent signals
    "process automation": 5,
    "robotic process automation": 6,
    "digitalisation": 4,
    "digitalization": 4,
    "digital transformation": 6,
    "consultancy": 3,
    "consulting services": 4,
    "business process": 5,
    "productivity": 3,
    "job redesign": 6,
    "service delivery": 3,
    "review of processes": 6,
}
MIN_SCORE = 5

SGT = timezone(timedelta(hours=8))  # M6 analogue: never use UTC for SG date strings


def sgt_today() -> str:
    return datetime.now(SGT).strftime("%Y-%m-%d")


def sgt_now_iso() -> str:
    return datetime.now(SGT).strftime("%Y-%m-%dT%H:%M:%S+08:00")


# ---------------------------------------------------------------------------
# Fetch — two paths, tried in order:
#   1. datastore_search API with pagination (JSON)
#   2. poll-download API -> signed CSV URL (whole file)
# ---------------------------------------------------------------------------

def http_get_json(url: str, timeout: int = 60) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "gebiz-radar/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_via_datastore() -> list[dict]:
    """Paginate through the datastore_search API. Returns list of raw row dicts."""
    rows, offset, limit = [], 0, 5000
    while True:
        params = urllib.parse.urlencode(
            {"resource_id": DATASET_ID, "limit": limit, "offset": offset}
        )
        data = http_get_json(f"{DATASTORE_URL}?{params}")
        if not data.get("success"):
            raise RuntimeError(f"datastore_search returned success=false: {data}")
        batch = data["result"].get("records", [])
        rows.extend(batch)
        total = data["result"].get("total", 0)
        offset += limit
        print(f"  datastore_search: {len(rows)}/{total} rows")
        if len(batch) < limit or len(rows) >= total:
            break
        time.sleep(1)  # be polite to the API
    return rows


def fetch_via_csv_download() -> list[dict]:
    """Ask the poll-download API for a signed CSV URL, then download and parse it."""
    for attempt in range(6):
        meta = http_get_json(POLL_DOWNLOAD_URL)
        url = (meta.get("data") or {}).get("url")
        if url:
            break
        print(f"  poll-download not ready (attempt {attempt + 1}), waiting 5s…")
        time.sleep(5)
    else:
        raise RuntimeError(f"poll-download never returned a URL: {meta}")

    req = urllib.request.Request(url, headers={"User-Agent": "gebiz-radar/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        text = resp.read().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def fetch_rows(local_path: str | None) -> list[dict]:
    if local_path:
        print(f"Reading local CSV: {local_path}")
        with open(local_path, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    print("Fetching via datastore_search API…")
    try:
        return fetch_via_datastore()
    except Exception as e:
        print(f"  datastore_search failed ({e}); falling back to CSV download…")
        return fetch_via_csv_download()


# ---------------------------------------------------------------------------
# Normalise, filter, group
# ---------------------------------------------------------------------------

# The dataset's column headers differ slightly between the API (snake_case)
# and the CSV export (Title Case). Normalise both.
FIELD_ALIASES = {
    "tender_no": ["tender_no", "Tender No", "tender no"],
    "description": ["tender_description", "Tender Description"],
    "agency": ["agency", "Agency"],
    "award_date": ["award_date", "Award Date"],
    "status": ["tender_detail_status", "Tender Detail Status"],
    "supplier": ["supplier_name", "Supplier Name"],
    "amount": ["awarded_amt", "Awarded Amt"],
}


def get_field(row: dict, key: str) -> str:
    for alias in FIELD_ALIASES[key]:
        if alias in row and row[alias] is not None:
            return str(row[alias]).strip()
    return ""


def parse_award_date(raw: str) -> str:
    """Dataset uses D/M/YYYY. Return ISO YYYY-MM-DD, or '' if unparseable."""
    raw = raw.strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def parse_amount(raw: str) -> float:
    try:
        return round(float(str(raw).replace(",", "").replace("$", "")), 2)
    except (ValueError, TypeError):
        return 0.0


def score_description(desc: str) -> tuple[int, list[str]]:
    """Return (score, matched keywords). Word-boundary matching, case-insensitive.

    Longest keywords are matched first and their text is masked out, so
    'lean six sigma' does not also score 'lean' and 'six sigma'."""
    low = desc.lower()
    score, hits = 0, []
    for kw in sorted(KEYWORDS, key=len, reverse=True):
        pattern = r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])"
        if re.search(pattern, low):
            score += KEYWORDS[kw]
            hits.append(kw)
            low = re.sub(pattern, "\u0000" * len(kw), low)  # mask matched span
    return score, hits


def build_tenders(rows: list[dict]) -> dict[str, dict]:
    """Filter rows by keyword score and group by tender number.

    One tender can have multiple rows (one per awarded supplier)."""
    tenders: dict[str, dict] = {}
    for row in rows:
        desc = get_field(row, "description")
        if not desc:
            continue
        score, hits = score_description(desc)
        if score < MIN_SCORE:
            continue

        tno = get_field(row, "tender_no")
        supplier = get_field(row, "supplier")
        amount = parse_amount(get_field(row, "amount"))
        status = get_field(row, "status")

        t = tenders.setdefault(tno, {
            "tenderNo": tno,
            "description": desc,
            "agency": get_field(row, "agency"),
            "awardDate": parse_award_date(get_field(row, "award_date")),
            "status": status,
            "score": score,
            "matched": hits,
            "suppliers": [],
            "totalAwarded": 0.0,
        })
        if supplier and supplier.lower() not in ("na", "n.a.", "nil", "unknown vendor"):
            t["suppliers"].append({"name": supplier, "amount": amount})
        t["totalAwarded"] = round(t["totalAwarded"] + amount, 2)
    return tenders


# ---------------------------------------------------------------------------
# Diff against previous run
# ---------------------------------------------------------------------------

def load_previous(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def apply_first_seen(tenders: dict[str, dict], previous: dict) -> tuple[int, int]:
    """Carry over firstSeen for known tenders; stamp today for new ones."""
    prev_map = {t["tenderNo"]: t for t in previous.get("tenders", [])}
    today = sgt_today()
    new_count = 0
    for tno, t in tenders.items():
        prev = prev_map.get(tno)
        if prev and prev.get("firstSeen"):
            t["firstSeen"] = prev["firstSeen"]
        else:
            t["firstSeen"] = today
            new_count += 1
    return new_count, len(prev_map)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def summarise_agencies(tenders: dict[str, dict]) -> list[dict]:
    agg: dict[str, dict] = {}
    for t in tenders.values():
        a = agg.setdefault(t["agency"], {"agency": t["agency"], "tenders": 0, "totalAwarded": 0.0})
        a["tenders"] += 1
        a["totalAwarded"] = round(a["totalAwarded"] + t["totalAwarded"], 2)
    return sorted(agg.values(), key=lambda x: -x["totalAwarded"])


def main() -> int:
    local_path = None
    if len(sys.argv) >= 3 and sys.argv[1] == "--local":
        local_path = sys.argv[2]

    rows = fetch_rows(local_path)
    print(f"Fetched {len(rows)} raw rows")

    tenders = build_tenders(rows)
    previous = load_previous(OUTPUT_PATH)
    new_count, prev_count = apply_first_seen(tenders, previous)

    tender_list = sorted(
        tenders.values(),
        key=lambda t: (t["awardDate"] or "0000-00-00", t["score"]),
        reverse=True,
    )

    output = {
        "meta": {
            "lastUpdated": sgt_now_iso(),
            "sourceDataset": DATASET_ID,
            "sourceRows": len(rows),
            "matchedTenders": len(tender_list),
            "newThisRun": new_count,
            "minScore": MIN_SCORE,
        },
        "agencies": summarise_agencies(tenders),
        "tenders": tender_list,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=1)

    print(f"Matched {len(tender_list)} tenders "
          f"({new_count} new vs previous {prev_count}) -> {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
