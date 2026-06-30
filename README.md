# NaPTAN Data-Quality Explorer

A browser-native data-quality explorer over the **entire** official UK NaPTAN dataset — all 435,029 public-transport stops from the DfT national export — queried live with DuckDB-WASM over a 9 MB parquet. No server, no API key, no upload: the full dataset is shipped in the repo and analysed in your tab.

**▶ Live demo: https://www.johnmikelregida.com/labs/naptan**

The row explorer and the free-form SQL box run entirely in your browser — they execute against the complete dataset client-side via WebAssembly, no backend and no API key. The aggregate dashboard above them is precomputed and inlined, so the headline numbers are exact on first paint. The only network calls are the CDN fetches for the engine and the one-time download of the parquet.

## What it is

NaPTAN (National Public Transport Access Nodes) is the canonical register of every bus stop, rail platform, ferry berth, and taxi rank in Great Britain. It is also a textbook case of a large, long-lived reference dataset that drifts: records go stale, statuses fall out of sync, localities go missing. This tool profiles the live national export for the failure modes that quietly break downstream routing, journey-planning, and accessibility systems — and lets you interrogate every individual record without leaving the page. It's aimed at anyone who has to *trust* a third-party reference dataset: data engineers, transport-tech teams, and anyone building data contracts on top of public-sector exports.

## How it works

The serving side is deliberately boring (static files); the data side is the interesting part (a real analytical engine running in the browser).

**The dashboard (precomputed, exact).** `refresh.py` pulls the full CSV from the DfT API, projects it into a typed columnar view in DuckDB, materialises a compact parquet, and computes every aggregate over all 435k rows. Those aggregates — quality-check counts, a sampled coverage map, percentages — are serialised to `data/naptan-stats.json` and inlined into the page at build time, so the headline numbers are correct on first paint with zero queries.

**The explorer (live, client-side).** On load, `index.html` dynamically imports `@duckdb/duckdb-wasm` from jsDelivr, spins up a Web Worker, registers the local `naptan.parquet` over HTTP, and creates a view:

```js
const duckdb = await import("https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm/+esm");
const bundle = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
const db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(), worker);
await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
await db.registerFileURL("naptan.parquet", PARQUET, duckdb.DuckDBDataProtocol.HTTP, false);
await conn.query("CREATE VIEW naptan AS SELECT * FROM read_parquet('naptan.parquet')");
```

From there, every interaction is a SQL query against the full dataset:

- **Filter chips** (`coord_bad`, `locality_bad`, `inactive`, `stale`) map directly to precomputed boolean columns baked into the parquet — no per-row recomputation at query time.
- **Full-text search** is an `ILIKE` across ATCO code, common name, and locality, debounced at 250 ms.
- **Pagination** is `LIMIT/OFFSET` with a `count(*)` for the match total (50 rows/page).
- **The SQL box** runs arbitrary user SQL, gated to `SELECT`/`WITH` only (regex-checked) and hard-capped at `LIMIT 200`. It's a genuine query surface over 435k rows, not a canned filter.

**The clever bit** is the cost model: pushing quality flags into the parquet at build time turns expensive predicate logic into cheap column scans, so the in-browser engine stays interactive over the whole dataset on the user's own CPU. The compute is the visitor's, not a server's — and DuckDB-WASM degrades gracefully (the page detects when the engine can't boot, e.g. in a restrictive embed, and keeps the exact aggregates while disabling the live explorer).

**The quality flags themselves** are defined once, in SQL, in `refresh.py`:

- `coord_bad` — null/unparseable lat-long, `(0,0)`, or outside the GB bounding box (lon −9.0…2.1, lat 49.0…61.1).
- `locality_bad` — empty `LocalityName`.
- `inactive` — `Status` present and not `active`.
- `stale` — `ModificationDateTime` older than a **dynamic 3-years-before-today** cutoff, so freshness stays honest on every run.
- Plus duplicate-ATCO and missing-common-name checks reported in the dashboard.

On the live data this surfaces ~63.7% stale, ~10.9% inactive, and **0** duplicate ATCO codes across all 435,029 records — the dataset is flawless on identity but ages badly on freshness.

**Staying fresh.** A GitHub Actions cron (`.github/workflows/daily-refresh.yml`, 06:00 UTC daily) runs `refresh.py` end-to-end — download → parquet → recompute stats → rebuild `index.html` — and commits the result, which auto-deploys via Vercel git integration. The raw multi-hundred-MB CSV is **not** committed (regenerated on demand); only the 9 MB ZSTD parquet and the stats JSON are versioned.

> Note: `analyze.py` is a standalone, pure-Python reference implementation of the same checks (single-pass CSV, no DuckDB dependency); `refresh.py` is the canonical pipeline CI runs.

## What the real data shows

Figures from the latest daily refresh of the official export. Percentages are over all 435,029 rows.

| Check | Records | Share |
|---|---:|---:|
| Stale — not modified in 3+ years | ~276,900 | **~63.7%** |
| Inactive / non-active status | ~47,300 | ~10.9% |
| Missing / invalid lat-long | ~37,100 | ~8.5% |
| Missing locality | ~18 | ~0.0% |
| Duplicate ATCO code | 0 | clean |
| Missing common name | 0 | clean |

Flawless on identifiers; the drift is almost entirely freshness.

## Why it matters

This is a small, concrete demonstration of three patterns that matter at platform scale. **Data contracts:** the quality checks are an executable specification of what "valid" means for a reference dataset, versioned alongside the data. **On-device compute:** shipping a columnar engine to the client and querying a 9 MB parquet in place is the same shape as edge analytics and privacy-preserving processing — the data and the compute go to the user, not the other way around. **Data quality as a first-class artifact:** drift in a reference dataset (stale rows, status skew) is exactly what silently corrupts the knowledge graphs and routing systems built on top of it, and making that drift queryable is the first step to governing it.

## Run it locally

These are static pages that fetch ES modules and WASM from CDNs, so they **must** be served over HTTP — opening `index.html` from `file://` will break the module imports and the parquet fetch.

```sh
git clone https://github.com/johnmikel/naptan-explorer
cd naptan-explorer
python3 -m http.server 8000
# open http://localhost:8000/index.html
```

Rebuild the page from the committed stats and template:

```sh
python3 build.py          # inlines data/naptan-stats.json into index.template.html -> index.html
```

Regenerate everything from the live national dataset (downloads ~97 MB CSV, rewrites the parquet, recomputes stats, rebuilds):

```sh
pip install duckdb
python3 refresh.py        # the same pipeline CI runs daily
```

The CI workflow (`.github/workflows/daily-refresh.yml`) does exactly this on a daily cron and commits the refreshed parquet + stats.

## Tech

- **Engine:** DuckDB-WASM (`@duckdb/duckdb-wasm`, loaded via jsDelivr `+esm`), running in a Web Worker.
- **Data format:** Apache Parquet, ZSTD-compressed (~9 MB, 435,029 rows, quality flags precomputed as columns).
- **Pipeline:** Python 3.12 + DuckDB (`refresh.py`); pure-Python reference checks (`analyze.py`); template inliner (`build.py`).
- **Frontend:** single self-contained `index.html` — vanilla JS, no framework, no build step beyond the inliner. Fraunces / Inter / IBM Plex Mono.
- **Automation:** GitHub Actions daily cron; static hosting (Vercel) with git-integration auto-deploy.
- **Data source:** Official NaPTAN national export, `naptan.api.dft.gov.uk` (Open Government Licence). Demo figures reflect the dataset as of the latest daily refresh.

---

Built by **John Mikel Regida** — Lead Data Architect (Thoughtworks; UK Dept for Transport / NaPTAN; ex-CTO; 5× Google Cloud Professional). GitHub: [github.com/johnmikel](https://github.com/johnmikel). Site: [https://www.johnmikelregida.com](https://www.johnmikelregida.com)

Part of the **JMR Labs** suite — [https://www.johnmikelregida.com/labs](https://www.johnmikelregida.com/labs)
