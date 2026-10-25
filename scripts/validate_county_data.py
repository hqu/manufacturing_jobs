#!/usr/bin/env python3
"""Independent, standard-library-only audit of the county CSV → v2 browser bundle.

Does not import the builder. Compares every observation, including nulls and
fractional precision. Optional --fixtures writes independent browser expectations.
"""
import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METRICS = ['emplvl_total', 'emplvl31_33', 'emplvl334', 'emplvl336']


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def load_bundle(path):
    text = path.read_text()
    prefix = 'window.MANUFACTURING_MAP_DATA'
    require(prefix in text, 'Bundle must assign window.MANUFACTURING_MAP_DATA')
    data, end = json.JSONDecoder().raw_decode(text.split(prefix, 1)[1].split('=', 1)[1].lstrip())
    require(data.get('schemaVersion') == 2, 'Expected schemaVersion 2')
    return data


def share(row, metric):
    return row[metric] / row[0] if row[metric] is not None and row[0] is not None and row[0] > 0 else None


def difference(before, after):
    return None if before is None or after is None else after - before


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', type=Path, default=ROOT.parent / '2026_10_23/QCEW_emplvl_byindustry_bymonth_mfg_county.csv')
    parser.add_argument('--bundle', type=Path, default=ROOT / 'manufacturing_map_data.js')
    parser.add_argument('--fixtures', type=Path)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'manufacturing_build_manifest.json')
    parser.add_argument('--geography', type=Path, default=ROOT.parent / '2026_10_23/tl_2020_us_county.zip')
    args = parser.parse_args()
    bundle = load_bundle(args.bundle)
    require(bundle['metrics'] == METRICS, 'Metric order changed')
    dates = bundle['dates']
    require(len(dates) == len(set(dates)), 'Duplicate dates')
    chronological = lambda d: (int(d[3:]), int(d[:2]))
    require(dates == sorted(dates, key=chronological), 'Dates are not chronological')
    require(dates == [f'{n % 12 + 1:02}/{n // 12}' for n in range(2002 * 12, 2026 * 12 + 3)], 'Source date bounds/continuity changed')
    require(bundle['latestDate'] == dates[-1], 'latestDate differs from actual final date')
    counties = bundle['counties']
    ids = [c['id'] for c in counties]
    require(ids == sorted(set(ids)), 'County IDs must be sorted and unique')
    require(all(len(c) == 5 and c.isdigit() for c in ids), 'County IDs lost leading zero or malformed')
    county_index = {c: i for i, c in enumerate(ids)}
    date_index = {d: i for i, d in enumerate(dates)}
    require(len(bundle['values']) == len(dates), 'Values date dimension mismatch')
    for month in bundle['values']:
        require(len(month) == len(ids), 'Values county dimension mismatch')
        for row in month:
            require(len(row) == 4, 'Values metric dimension mismatch')
            require(all(v is None or (type(v) in (float, int) and math.isfinite(v) and v >= 0) for v in row), 'Invalid number in bundle')
    missing, zeros, fractions = Counter(), Counter(), Counter()
    seen = set()
    fixtures = {'examples': {}, 'endpoints': [], 'aggregate': {}}
    source_names = {}
    source_endpoints = {}
    source_aggregate = {d: {mi: [0, 0, 0, 0, 0] for mi in range(1, 4)} for d in (dates[0], dates[-1])}
    with args.csv.open(newline='') as handle:
        reader = csv.DictReader(handle)
        for source in reader:
            cid, date = source['cty_id'], source['Date']
            require(cid in county_index and date in date_index, f'Unrepresented source identity {cid}/{date}')
            key = (cid, date)
            require(key not in seen, f'Duplicate source county-month {key}')
            seen.add(key)
            source_names[cid] = source['cty_name']
            expected = [None if source[m].strip() == '' else float(source[m]) for m in METRICS]
            if date in source_aggregate:
                source_endpoints[(cid, date)] = expected
                for mi in range(1, 4):
                    sums = source_aggregate[date][mi]
                    if expected[mi] is not None:
                        sums[0] += expected[mi]
                        sums[1] += 1
                    if share(expected, mi) is not None:
                        sums[2] += expected[mi]
                        sums[3] += expected[0]
                        sums[4] += 1
            actual = bundle['values'][date_index[date]][county_index[cid]]
            require(actual == expected, f'Value/precision mismatch {key}: {actual} != {expected}')
            for i, value in enumerate(expected):
                category = 'missing' if value is None else 'zero' if value == 0 else 'fractional' if not value.is_integer() else None
                if category:
                    {'missing': missing, 'zero': zeros, 'fractional': fractions}[category][METRICS[i]] += 1
                    fixtures['examples'].setdefault(f'{category}:{METRICS[i]}', {'id': cid, 'name': source['cty_name'], 'date': date, 'metric': METRICS[i], 'count': value, 'share': share(expected, i)})
    require(len(seen) == len(ids) * len(dates), 'Source is not a complete county-month grid')
    require(len(ids) == 3108 and len(seen) == 904428, 'Unexpected source scope')
    require([missing[m] for m in METRICS] == [4146, 90237, 255690, 346512], 'Unexpected source missing counts')
    require(all(c['name'] == source_names[c['id']] for c in counties), 'County name mismatch')
    features = bundle['geojson']['features']
    geoids = [f['properties']['county_id'] for f in features]
    require(sorted(geoids) == ids, 'Geometry join is not exactly one feature per source county')
    types = Counter()
    holes = 0
    bounds = [180, 90, -180, -90]
    for feature in features:
        cid = feature['properties']['county_id']
        require(feature['properties']['county_name'] == source_names[cid], f'Geometry name mismatch {cid}')
        geom = feature['geometry']
        types[geom['type']] += 1
        require(geom['type'] in ('Polygon', 'MultiPolygon'), f'Non-area geometry {cid}')
        polygons = [geom['coordinates']] if geom['type'] == 'Polygon' else geom['coordinates']
        require(polygons, f'Empty geometry {cid}')
        for polygon in polygons:
            require(polygon, f'Empty polygon {cid}')
            holes += max(0, len(polygon) - 1)
            for ring in polygon:
                require(len(ring) >= 4 and ring[0] == ring[-1], f'Invalid/unclosed ring {cid}')
                for lon, lat, *rest in ring:
                    require(math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90, f'Invalid WGS84 coordinate {cid}')
                    bounds = [min(bounds[0], lon), min(bounds[1], lat), max(bounds[2], lon), max(bounds[3], lat)]
    require(types['MultiPolygon'] > 0, 'Multipart geometry lost')
    require(holes > 0, 'All geometry holes lost')
    # Representative southern/eastern/western counties must retain geographic placement.
    for cid, expected_bounds in {'01001': (-87, 32, -86, 33), '06037': (-119, 32, -117, 35), '36061': (-75, 40, -73, 42)}.items():
        feature = next(f for f in features if f['properties']['county_id'] == cid)
        geom = feature['geometry']
        def coordinates(value):
            if isinstance(value[0], (int, float)):
                yield value
            else:
                for child in value:
                    yield from coordinates(child)
        points = list(coordinates(geom['coordinates']))
        center = (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))
        require(expected_bounds[0] < center[0] < expected_bounds[2] and expected_bounds[1] < center[1] < expected_bounds[3], f'Representative county misplaced: {cid}')
    # Evaluate all count/share endpoint changes independently; browser fixtures include ordinary and unavailable cases.
    for ci, cid in enumerate(ids):
        first, last = source_endpoints[(cid, dates[0])], source_endpoints[(cid, dates[-1])]
        if cid in ('01001', '06037', '36061'):
            for mi in range(1, 4):
                fixtures['endpoints'].append({'id': cid, 'metric': METRICS[mi], 'start': dates[0], 'end': dates[-1], 'count': last[mi], 'share': share(last, mi), 'countChange': difference(first[mi], last[mi]), 'shareChange': difference(share(first, mi), share(last, mi))})
    for date, metric_sums in source_aggregate.items():
        fixtures['aggregate'][date] = {}
        for mi, sums in metric_sums.items():
            fixtures['aggregate'][date][METRICS[mi]] = {'count': sums[0], 'countCoverage': sums[1], 'share': sums[2] / sums[3], 'shareCoverage': sums[4]}
    fixtures['sourceSHA256'] = hashlib.sha256(args.csv.read_bytes()).hexdigest()
    manifest = json.loads(args.manifest.read_text())
    require(manifest['schemaVersion'] == 2, 'Manifest schema mismatch')
    require(manifest['provenance'] == bundle['provenance'], 'Bundle/manifest provenance mismatch')
    require(bundle['provenance']['employmentSource']['sha256'] == fixtures['sourceSHA256'], 'CSV provenance hash mismatch')
    require(bundle['provenance']['geographySource']['sha256'] == hashlib.sha256(args.geography.read_bytes()).hexdigest(), 'Geography provenance hash mismatch')
    require(manifest['output']['sha256'] == hashlib.sha256(args.bundle.read_bytes()).hexdigest(), 'Bundle manifest hash mismatch')
    require(manifest['output']['bytes'] == args.bundle.stat().st_size, 'Bundle manifest byte size mismatch')
    for metric in METRICS:
        expected_stats = {'observed': len(seen) - missing[metric], 'null': missing[metric], 'zero': zeros[metric], 'fractional': fractions[metric]}
        require(manifest['source']['metrics'][metric] == expected_stats, f'Manifest source statistics mismatch: {metric}')
    if args.fixtures:
        args.fixtures.write_text(json.dumps(fixtures, indent=2) + '\n')
    print(json.dumps({'status': 'PASS', 'rowsCompared': len(seen), 'valuesCompared': len(seen) * 4, 'counties': len(ids), 'months': len(dates), 'missing': dict(missing), 'zeros': dict(zeros), 'fractional': dict(fractions), 'geometryTypes': dict(types), 'holes': holes, 'bounds': bounds, 'bundleBytes': args.bundle.stat().st_size, 'sourceSHA256': fixtures['sourceSHA256']}, indent=2))


if __name__ == '__main__':
    main()
