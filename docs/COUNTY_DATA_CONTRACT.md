# County dashboard data contract (v2)

## Sources and scope

Read `../QCEW_emplvl_byindustry_bymonth_mfg_county.csv` and
`../tl_2020_us_county.zip` by default, with CLI overrides. Clip county
geometry to land with `cb_2018_us_state_500k.zip` (no lake or coastal water).
The CSV controls which counties are included. Join its five-character `cty_id`
to 2020 geography `GEOID` directly. Keep source precision. No new crosswalk.
Expected source: 3,108 counties, 291 months (01/2002–03/2026), 904,428 rows.

## Generated interface

`manufacturing_map_data.js` assigns `window.MANUFACTURING_MAP_DATA` to:

```js
{
  schemaVersion: 2,
  dates: ["01/2002", /* chronological */ "03/2026"],
  latestDate: "03/2026",
  metrics: ["emplvl_total", "emplvl31_33", "emplvl334", "emplvl336"],
  counties: [{id: "01001", name: "Autauga County, AL"}], // sorted by id
  values: [ /* date index */ [ /* county index */ [ /* metric index */ ] ] ],
  geojson: {type: "FeatureCollection", features: [
    {type: "Feature", properties: {county_id: "01001", county_name: "Autauga County, AL"}, geometry: { /* EPSG:4326 */ }}
  ]},
  provenance: { /* source identities, SHA256, scope, vintage, units, missing semantics */ }
}
```

`values[dateIndex][countyIndex]` is exactly four values in `metrics` order.
Each value is a finite nonnegative number or `null`. Geometry has one feature per
included county, with topology-aware simplification that preserves meaningful
holes and multipart geometry. Subpixel islands/holes may be removed if measured
and documented; no county feature may be lost. Extra feature properties
(such as a label anchor) may be added without changing the required fields.
Do not eagerly expand every observation into verbose record objects in the browser.
Keep source hashes/build settings/statistics in a generated build manifest.

## Measurement and presentation rules

- Blank CSV cells mean **Data missing**, as confirmed by the user. Never
  coerce them to zero or describe them as suppressed. Explicit zero stays zero.
- A sector employment share is sector / total only if both are observed and
  total > 0. Store/compute shares as fractions; display as percentages.
- Comparisons subtract earlier from later: jobs for counts, percentage points
  for shares. Both endpoints must be observed.
- Missing values are excluded from map domains and rankings; charts show gaps.
  County identity/selection remains available even if the selected measure is missing.
- Aggregate count is the sum of reported selected-sector observations, with
  county coverage. Aggregate share uses the same matched county population for
  numerator and denominator (both observed, positive total), with coverage.
  Document that coverage changes can affect trends; do not claim complete totals.
- User does not want Alaska/Hawaii work or 'Contiguous' in the title. Use a
  concise aggregate label such as 'Reported county employment'; do not call an
  incomplete county sum an official national estimate.
- Preserve the existing dashboard composition. Replace user-facing CZ terminology
  with county terminology. Step 1 is "Select Employment Metric" (Count of jobs /
  Share of total county jobs); Step 2 is "Select Industry" (All manufacturing /
  Electronics manufacturing / Transportation equipment manufacturing).
- Zero employment is drawn as "No jobs", distinct from both the color scale and
  "Data missing". No log-scale note in the legend.
- All date bounds and axes derive from actual dates. Default to latest month.

## Verification and ownership

Worker A owns build scripts, dependency declarations, generated bundle/manifest.
Worker B owns index.html and application modules/styles.
Worker C owns validation/browser checks and README. Root owns this contract.
Coordinate contract changes through root. No commits, pushes, or deployments.
Final independent review is GPT-6.1 Sol Ultra after Medium implementation.

Targets to measure: bundle <=25 MB uncompressed, initial usability <=5 seconds,
ordinary metric/date update <=250 ms, hover <=100 ms on this machine. Record
actual measurements and limitations; do not claim targets passed without evidence.
