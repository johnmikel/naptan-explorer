#!/usr/bin/env python3
"""
Daily refresh pipeline for the NaPTAN Data-Quality Explorer.

  1. Download the full national NaPTAN CSV from the official DfT API (~97 MB).
  2. Build a compact Parquet (all 435k rows + precomputed quality flags, ZSTD ~9 MB).
  3. Compute full-dataset aggregates -> data/naptan-stats.json.
  4. Rebuild the self-contained index.html (python build.py).

Staleness is computed dynamically (3 years before *today*), so it stays correct
on every run. Requires: duckdb  (pip install duckdb).

  python3 refresh.py
"""
import duckdb, json, os, subprocess, urllib.request
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
URL = "https://naptan.api.dft.gov.uk/v1/access-nodes?dataFormat=csv"
CSV = os.path.join(ROOT, "data", "naptan-full.csv")
PARQUET = os.path.join(ROOT, "naptan.parquet")
STATS = os.path.join(ROOT, "data", "naptan-stats.json")
os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)

today = date.today()
cutoff = today.replace(year=today.year - 3).isoformat()   # stale = older than 3y

print("1/4 downloading national NaPTAN CSV…")
urllib.request.urlretrieve(URL, CSV)
print("    %.0f MB" % (os.path.getsize(CSV) / 1048576))

con = duckdb.connect()
con.execute(f"CREATE VIEW raw AS SELECT * FROM read_csv('{CSV}', header=true, all_varchar=true, sample_size=-1)")
con.execute(f"""
CREATE VIEW c AS SELECT
  "ATCOCode" AS atco, "CommonName" AS name, "LocalityName" AS locality,
  lower(coalesce("Status",'')) AS status, "StopType" AS stoptype,
  substr("ModificationDateTime",1,10) AS modified, "AdministrativeAreaCode" AS area,
  TRY_CAST("Longitude" AS DOUBLE) AS lon, TRY_CAST("Latitude" AS DOUBLE) AS lat,
  (TRY_CAST("Longitude" AS DOUBLE) IS NULL OR TRY_CAST("Latitude" AS DOUBLE) IS NULL
     OR (TRY_CAST("Longitude" AS DOUBLE)=0 AND TRY_CAST("Latitude" AS DOUBLE)=0)
     OR TRY_CAST("Longitude" AS DOUBLE) < -9.0 OR TRY_CAST("Longitude" AS DOUBLE) > 2.1
     OR TRY_CAST("Latitude" AS DOUBLE) < 49.0 OR TRY_CAST("Latitude" AS DOUBLE) > 61.1) AS coord_bad,
  (coalesce(trim("LocalityName"),'')='') AS locality_bad,
  (lower(coalesce("Status",'')) NOT IN ('active','')) AS inactive,
  (length(substr("ModificationDateTime",1,10))=10 AND substr("ModificationDateTime",1,10) < '{cutoff}') AS stale
FROM raw
""")

print("2/4 writing parquet…")
con.execute(f"COPY (SELECT * FROM c) TO '{PARQUET}' (FORMAT PARQUET, COMPRESSION ZSTD)")

print("3/4 computing aggregates…")
g = lambda q: con.sql(q).fetchone()[0]
total = g("SELECT count(*) FROM c")
coord = g("SELECT count(*) FROM c WHERE coord_bad")
loc = g("SELECT count(*) FROM c WHERE locality_bad")
inact = g("SELECT count(*) FROM c WHERE inactive")
stale = g("SELECT count(*) FROM c WHERE stale")
active = g("SELECT count(*) FROM c WHERE status='active'")
dupe = g("SELECT coalesce(sum(ct),0) FROM (SELECT count(*) ct FROM c GROUP BY atco HAVING count(*)>1)")
nm = g("SELECT count(*) FROM c WHERE coalesce(trim(name),'')=''")
flagged = g("SELECT count(*) FROM c WHERE coord_bad OR locality_bad OR inactive OR stale")
pct = lambda a: round(a / total * 100, 1)

map_pts = con.sql("""SELECT round(lon,3), round(lat,3),
  CASE WHEN coord_bad OR locality_bad OR inactive OR stale THEN 1 ELSE 0 END
  FROM (SELECT *, row_number() OVER () rn FROM c) WHERE NOT coord_bad AND rn % 80 = 0""").fetchall()
sample = con.sql("""SELECT atco,name,locality,status,modified,coord_bad,locality_bad,inactive,stale
  FROM c WHERE coord_bad OR locality_bad OR inactive OR stale LIMIT 60""").fetchall()
def issues(r):
    keys = ["coord", "locality", "inactive", "stale"]
    return [keys[i] for i, v in enumerate(r[5:9]) if v]

stats = {
    "source": "Official NaPTAN national dataset (naptan.api.dft.gov.uk)",
    "asOf": today.isoformat(),
    "totalStops": total,
    "metrics": {"validCoordsPct": pct(total - coord), "activePct": pct(active),
                "withLocalityPct": pct(total - loc), "flaggedTotal": flagged, "flaggedPct": pct(flagged)},
    "checks": [
        {"key": "coord", "nm": "Missing / invalid coordinates", "count": coord, "pct": pct(coord)},
        {"key": "locality", "nm": "Missing locality", "count": loc, "pct": pct(loc)},
        {"key": "inactive", "nm": "Inactive / non-active status", "count": inact, "pct": pct(inact)},
        {"key": "stale", "nm": "Stale (not modified in 3y+)", "count": stale, "pct": pct(stale)},
        {"key": "dupe", "nm": "Duplicate ATCO code", "count": dupe, "pct": pct(dupe)},
        {"key": "name", "nm": "Missing common name", "count": nm, "pct": pct(nm)},
    ],
    "mapPoints": [[p[0], p[1], p[2]] for p in map_pts],
    "sampleFlagged": [{"atco": r[0], "name": r[1] or "—", "locality": r[2] or "",
                       "status": r[3] or "—", "mod": r[4] or "—", "issues": issues(r)} for r in sample],
}
json.dump(stats, open(STATS, "w"))
print(f"    {total:,} stops · {flagged:,} flagged · stale<{cutoff}={stale:,}")

print("4/4 building index.html…")
subprocess.run(["python3", os.path.join(ROOT, "build.py")], check=True)
print("done.")
