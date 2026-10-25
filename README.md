# Manufacturing employment by county

An interactive Leaflet dashboard showing manufacturing, electronics, and transportation equipment employment across 3,108 source counties, monthly from January 2002 through March 2026. The default view compares the first available month (January 2002) with the latest (March 2026). Close comparison to explore a single month. Select Employment Count or Employment Share, choose a sector and month, or compare two months. Hover over counties or ranking rows, click to pin a county, and use Reset map to restore the overview.

## View locally

The prebuilt `manufacturing_map_data.js` is included. Serve this directory, then open [the dashboard](http://127.0.0.1:4173):

```sh
python3 -m http.server 4173 --bind 127.0.0.1
```

Leaflet 1.9.4 and its license are included under `vendor/leaflet`; the dashboard uses local assets and system fonts, so previewing it requires no external network requests. A static host is sufficient; there is no application backend. After `npm ci`, run `npm run vendor:leaflet` to refresh the bundled Leaflet files from the pinned package.

## Data and interpretation

The source is `../2026_10_23/QCEW_emplvl_byindustry_bymonth_mfg_county.csv`: 904,428 county-month rows and four employment columns. `cty_id` is a five-character county identifier, including leading zeros. Its county set controls inclusion. The geography source is `../2026_10_23/tl_2020_us_county.zip`; the builder joins `cty_id` directly to 2020 TIGER `GEOID`, without a new crosswalk. County boundaries are reprojected to WGS84 and simplified for browser display, retaining multipart areas and holes at the chosen display resolution; very small components can collapse. The bundled geometry represents the source counties, rather than every county in the geography file.

- `emplvl_total`: total employment, the denominator for shares.
- `emplvl31_33`: manufacturing employment.
- `emplvl334`: electronics employment.
- `emplvl336`: transportation equipment employment.

Source numbers retain fractional precision. Displayed counts are rounded for readability. Blank cells mean **Data missing**; observed zero remains zero. The four columns contain 4,146, 90,237, 255,690, and 346,512 blanks respectively. Missing data is excluded from map scales and rankings, county selection remains available, and county trend lines break at unavailable months.

Employment Share is sector employment divided by total employment only when both values are available and total employment is positive. Shares are stored/computed as fractions and displayed as percentages. Comparison values subtract the earlier month from the later month: jobs for counts and percentage points for shares. An unavailable endpoint makes the comparison unavailable.

The **Reported county employment** trend sums observed selected-sector counts and shows county coverage. Aggregate shares use the same matched counties for numerator and denominator: both observations must be available and total employment must be positive. Coverage can change by month and sector, so coverage changes can affect trends. These sums are not an official national estimate.

## Rebuild

Use Python 3.10+, Node.js, and npm. The source CSV and ZIP must be present at the paths above, or supplied with flags.

```sh
npm ci
npm run build:data
```

The builder uses pinned `mapshaper` to create `manufacturing_map_data.js` and `manufacturing_build_manifest.json`. The manifest records source SHA256 hashes, source counts, missing/zero/fractional statistics, geometry checks, and build settings. The bundle uses the compact v2 interface in [the data contract](docs/COUNTY_DATA_CONTRACT.md): chronological dates, sorted counties, a date × county × metric array, geometry, and provenance. It does not expand every observation into browser record objects.

```sh
python3 scripts/build_county_data.py --help
python3 scripts/build_county_data.py --csv /path/to/source.csv --geography /path/to/counties.zip
```

The default simplification maximum is 200 metres, reduced for small counties using one percent of the square root of county area; shared arcs use the smaller adjoining interval. Output coordinates use five decimal places. Source employment values are not rounded by geometry processing.

## Validate

The independent validator imports no builder code and uses only Python's standard library. It compares every one of the 3,617,712 bundle values directly to the CSV, checks unavailable/zero/fractional values, unique county-month identities, date coverage, compact array dimensions, geometry joins, closed rings, multipart shapes, holes, and representative county placement.

```sh
python3 scripts/validate_county_data.py
python3 scripts/validate_county_data.py --fixtures /tmp/county-fixtures.json
```

The browser QA script requires an available Playwright installation and browser. The Browser plugin is not available in this environment, so QA uses regular Playwright. With the local server running:

```sh
BASE_URL=http://127.0.0.1:4173 BROWSER_CHANNEL=chrome \
COUNTY_FIXTURES=/tmp/county-fixtures.json \
node tests/county_browser_qa.cjs
```

If Playwright is supplied by a shared runtime, set `NODE_PATH` to its `node_modules` directory and invoke its Node executable. Omit `BROWSER_CHANNEL` to use Playwright's installed Chromium. `QA_OUTPUT_DIR` defaults to `/tmp/county-dashboard-qa`; screenshots and JSON results stay outside the repository.

Browser checks cover all six metric/measure views, every county's current rankings and comparison arithmetic, unavailable fixtures, zero/fractional fixtures, trend gaps, date bounds and both comparison handles, ranking hover/pinning, reset, legend, desktop/mobile screenshots, horizontal overflow, and console errors. Results include observed timings; interaction timings include automation overhead and are not microbenchmarks. Performance targets are a bundle at most 25 MB uncompressed, initial usability within five seconds, ordinary updates within 250 ms, and hover within 100 ms on the test machine. A target is only verified when measured results establish it.
