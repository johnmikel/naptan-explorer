#!/usr/bin/env python3
import csv, json, collections

SRC = "/private/tmp/claude-501/-Users-johnmikelregida-Desktop-projects-john-mikel-portfolio/52e9a89e-a3db-42b1-9eab-bf35c0c94c8d/scratchpad/naptan-full.csv"
OUT = "/private/tmp/claude-501/-Users-johnmikelregida-Desktop-projects-john-mikel-portfolio/52e9a89e-a3db-42b1-9eab-bf35c0c94c8d/scratchpad/naptan-stats.json"
AS_OF = "2026-06-29"
STALE_BEFORE = "2023-06-29"   # 3 years

def is_num(x):
    try:
        float(x); return True
    except: return False

# bounds for GB (England, Scotland incl Shetland, Wales)
def coord_ok(lon, lat):
    if not (is_num(lon) and is_num(lat)): return False
    lo, la = float(lon), float(lat)
    if lo == 0 and la == 0: return False
    return (-9.0 <= lo <= 2.1) and (49.0 <= la <= 61.1)

# ---- pass 1: ATCO frequency ----
atco = collections.Counter()
with open(SRC, newline='', encoding='utf-8', errors='replace') as f:
    for r in csv.DictReader(f):
        atco[r["ATCOCode"]] += 1
dupe_atcos = {a for a, c in atco.items() if c > 1 and a}
dupe_rows = sum(c for a, c in atco.items() if c > 1 and a)

# ---- pass 2: quality checks + samples ----
total = 0
coord_bad = locality_bad = inactive = stale = name_bad = 0
status_ct = collections.Counter()
stoptype_ct = collections.Counter()
area_ct = collections.Counter()
samples = collections.defaultdict(list)   # issue -> rows
map_pts = []
SAMPLE_EVERY = 80   # ~5.4k points
i = 0
with open(SRC, newline='', encoding='utf-8', errors='replace') as f:
    for r in csv.DictReader(f):
        i += 1; total += 1
        lon, lat = r["Longitude"], r["Latitude"]
        ok_coord = coord_ok(lon, lat)
        loc = r["LocalityName"].strip()
        st = (r["Status"] or "").strip().lower()
        mod = (r["ModificationDateTime"] or "")[:10]
        nm = r["CommonName"].strip()
        issues = []
        if not ok_coord: coord_bad += 1; issues.append("coord")
        if not loc: locality_bad += 1; issues.append("locality")
        if st and st != "active": inactive += 1; issues.append("inactive")
        if mod and mod < STALE_BEFORE: stale += 1; issues.append("stale")
        if not nm: name_bad += 1; issues.append("name")
        if r["ATCOCode"] in dupe_atcos: issues.append("dupe")
        status_ct[st or "(blank)"] += 1
        stoptype_ct[r["StopType"] or "(blank)"] += 1
        area_ct[r["AdministrativeAreaCode"]] += 1
        # collect up to 12 example rows per issue
        for k in issues:
            if len(samples[k]) < 12:
                samples[k].append({
                    "atco": r["ATCOCode"], "name": nm or "—",
                    "locality": loc or "", "status": st or "—",
                    "mod": mod or "—", "issues": issues[:]
                })
        # map sample (only plottable points), every Nth
        if ok_coord and (i % SAMPLE_EVERY == 0):
            map_pts.append([round(float(lon), 3), round(float(lat), 3), 1 if issues else 0])

flagged_total = sum(1 for _ in ())  # placeholder
# recompute flagged_total properly (any issue). We didn't store per-row; derive via union counts is wrong.
# Do a quick third pass for exact "any issue" count.
flagged_total = 0
with open(SRC, newline='', encoding='utf-8', errors='replace') as f:
    for r in csv.DictReader(f):
        lon, lat = r["Longitude"], r["Latitude"]
        bad = (not coord_ok(lon, lat)) or (not r["LocalityName"].strip()) \
              or ((r["Status"] or "").strip().lower() not in ("active", "")) \
              or (((r["ModificationDateTime"] or "")[:10]) and ((r["ModificationDateTime"] or "")[:10] < STALE_BEFORE)) \
              or (not r["CommonName"].strip()) or (r["ATCOCode"] in dupe_atcos)
        if bad: flagged_total += 1

def pct(a): return round(a / total * 100, 1)

# dedup sample table: build a mixed list (a few from each category)
table = []
seen = set()
for k in ["coord", "inactive", "dupe", "locality", "stale", "name"]:
    for row in samples.get(k, []):
        key = row["atco"] + row["name"]
        if key not in seen:
            seen.add(key); table.append(row)
table = table[:60]

result = {
    "source": "Official NaPTAN national dataset (naptan.api.dft.gov.uk)",
    "asOf": AS_OF,
    "totalStops": total,
    "metrics": {
        "validCoordsPct": pct(total - coord_bad),
        "activePct": pct(status_ct.get("active", 0)),
        "withLocalityPct": pct(total - locality_bad),
        "flaggedTotal": flagged_total,
        "flaggedPct": pct(flagged_total),
    },
    "checks": [
        {"key": "coord", "nm": "Missing / invalid coordinates", "count": coord_bad, "pct": pct(coord_bad)},
        {"key": "locality", "nm": "Missing locality", "count": locality_bad, "pct": pct(locality_bad)},
        {"key": "inactive", "nm": "Inactive / non-active status", "count": inactive, "pct": pct(inactive)},
        {"key": "stale", "nm": "Stale (not modified since " + STALE_BEFORE[:4] + "-06)", "count": stale, "pct": pct(stale)},
        {"key": "dupe", "nm": "Duplicate ATCO code", "count": dupe_rows, "pct": pct(dupe_rows)},
        {"key": "name", "nm": "Missing common name", "count": name_bad, "pct": pct(name_bad)},
    ],
    "statusBreakdown": dict(status_ct.most_common()),
    "stopTypeTop": stoptype_ct.most_common(8),
    "areaCount": len(area_ct),
    "uniqueAtco": len(atco),
    "dupeAtcoCodes": len(dupe_atcos),
    "mapPoints": map_pts,
    "sampleFlagged": table,
}
json.dump(result, open(OUT, "w"))

# ---- print summary ----
print("TOTAL STOPS:", f"{total:,}")
print("Unique ATCO:", f"{len(atco):,}", "| Duplicate ATCO codes:", f"{len(dupe_atcos):,}", "| rows w/ dupe atco:", f"{dupe_rows:,}")
print("Admin areas:", len(area_ct))
print("Flagged (any issue):", f"{flagged_total:,}", f"({pct(flagged_total)}%)")
print("\nCHECKS:")
for c in result["checks"]:
    print(f"  {c['nm']:42} {c['count']:>8,}  ({c['pct']}%)")
print("\nSTATUS:", dict(status_ct.most_common()))
print("TOP STOPTYPES:", stoptype_ct.most_common(8))
print("map sample points:", len(map_pts), "| table rows:", len(table))
print("\nwrote", OUT)
