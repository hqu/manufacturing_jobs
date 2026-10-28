#!/usr/bin/env python3
"""Build the version-2 county bundle from the original QCEW CSV and TIGER ZIP.

Requires Python 3.10+ and npm ci (pinned mapshaper). No Python dependencies.
All numeric CSV tokens are emitted unchanged: decimal precision, zero and blanks
are preserved. Repeated builds from identical sources/settings are byte-identical.
"""
import argparse
import csv
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
METRICS = ['emplvl_total', 'emplvl31_33', 'emplvl334', 'emplvl336']
HEADER = ['cty_fips', 'cty_name', 'Date', *METRICS, 'cty_id']
NUMBER = re.compile(r'(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z')


def require(test, message):
    if not test:
        raise ValueError(message)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def month_key(value):
    require(bool(re.fullmatch(r'(?:0[1-9]|1[0-2])/[0-9]{4}', value)), f'Invalid date: {value!r}')
    month, year = map(int, value.split('/'))
    require(year >= 1900, f'Invalid year: {value!r}')
    return year * 12 + month - 1


def read_csv(path, expected_counties, expected_months):
    records, names, dates = {}, {}, set()
    stats = {m: {'observed': 0, 'null': 0, 'zero': 0, 'fractional': 0} for m in METRICS}
    with path.open(newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        require(reader.fieldnames == HEADER, f'Unexpected CSV header: {reader.fieldnames!r}')
        for line, row in enumerate(reader, 2):
            require(None not in row and all(v is not None for v in row.values()), f'Malformed CSV row {line}')
            county, date, name = row['cty_id'], row['Date'], row['cty_name']
            require(bool(re.fullmatch('[0-9]{5}', county)), f'Invalid cty_id at row {line}: {county!r}')
            require(row['cty_fips'].isdigit() and int(row['cty_fips']) == int(county), f'cty_fips mismatch at row {line}')
            require(bool(name.strip()) and name == name.strip(), f'Missing/unclean name at row {line}')
            require(county not in names or names[county] == name, f'County name changes for {county}')
            month_key(date)
            key = (date, county)
            require(key not in records, f'Duplicate county/date at row {line}: {key}')
            values = []
            for metric in METRICS:
                token = row[metric]
                if token == '':
                    values.append('null')
                    stats[metric]['null'] += 1
                    continue
                require(bool(NUMBER.fullmatch(token)), f'Invalid {metric} at row {line}: {token!r}')
                value = Decimal(token)
                require(value.is_finite() and value >= 0 and math.isfinite(float(value)), f'Nonfinite/negative value at row {line}')
                # Ensure JS can represent integer employment counts exactly.
                require(value <= 9007199254740991, f'Value exceeds JavaScript safe range at row {line}')
                stats[metric]['observed'] += 1
                stats[metric]['zero'] += int(value == 0)
                stats[metric]['fractional'] += int(value != value.to_integral_value())
                values.append(token)
            records[key] = '[' + ','.join(values) + ']'
            names[county] = name
            dates.add(date)
    ordered_dates = sorted(dates, key=month_key)
    require(bool(ordered_dates), 'CSV contains no observations')
    keys = list(map(month_key, ordered_dates))
    require(keys == list(range(keys[0], keys[-1] + 1)), 'Dates must be consecutive months')
    counties = sorted(names)
    require(len(counties) == expected_counties, f'Expected {expected_counties} counties, found {len(counties)}')
    require(len(dates) == expected_months, f'Expected {expected_months} months, found {len(dates)}')
    require(len(records) == len(counties) * len(dates), 'Incomplete county/month grid')
    for date in ordered_dates:
        for county in counties:
            require((date, county) in records, f'Missing county/date row: {county}/{date}')
    return records, names, ordered_dates, counties, stats


def ring_area(ring):
    # Spherical area in square metres; same formula before/after simplification.
    total = 0.0
    for a, b in zip(ring, ring[1:]):
        lon_delta = math.radians(b[0] - a[0])
        if lon_delta > math.pi:
            lon_delta -= 2 * math.pi
        elif lon_delta < -math.pi:
            lon_delta += 2 * math.pi
        total += lon_delta * (2 + math.sin(math.radians(a[1])) + math.sin(math.radians(b[1])))
    return total * 6378137.0 ** 2 / 2


def geometry_stats(rings):
    areas = [ring_area(r) for r in rings]
    return {'rings': len(rings), 'vertices': sum(map(len, rings)), 'area_m2': abs(sum(areas))}


def source_geometry(zip_path):
    # Minimal, read-only DBF/SHP parser, restricted to the supplied polygon source.
    with zipfile.ZipFile(zip_path) as z:
        shp_names = [n for n in z.namelist() if n.endswith('.shp')]
        require(len(shp_names) == 1, 'Expected exactly one shapefile in ZIP')
        stem = shp_names[0][:-4]
        prj = z.read(stem + '.prj').decode('utf-8')
        require('GCS_North_American_1983' in prj and 'Degree' in prj, 'Unexpected source CRS; expected TIGER NAD83 longitude/latitude')
        dbf = z.read(stem + '.dbf')
        count, header_len, record_len = struct.unpack_from('<IHH', dbf, 4)
        fields = []
        for pos in range(32, header_len - 1, 32):
            if dbf[pos] == 13:
                break
            fields.append((dbf[pos:pos+11].split(b'\0')[0].decode('ascii'), dbf[pos+16]))
        ids = []
        for n in range(count):
            pos = header_len + n * record_len
            require(dbf[pos] == 32, 'Deleted source DBF record')
            pos += 1
            record = {}
            for name, length in fields:
                record[name] = dbf[pos:pos+length].decode('utf-8').strip()
                pos += length
            ids.append(record['GEOID'])
        require(len(set(ids)) == len(ids), 'Duplicate source geography GEOIDs')
        stats = {}
        with z.open(stem + '.shp') as shp:
            header = shp.read(100)
            require(struct.unpack_from('<I', header, 32)[0] == 5, 'Expected polygon shapefile')
            for county in ids:
                record_header = shp.read(8)
                require(len(record_header) == 8, 'Missing SHP record')
                length = struct.unpack('>II', record_header)[1] * 2
                record = shp.read(length)
                require(struct.unpack_from('<I', record)[0] == 5, f'Nonpolygon source geometry: {county}')
                parts, points = struct.unpack_from('<II', record, 36)
                starts = list(struct.unpack_from('<' + 'I' * parts, record, 44)) + [points]
                coords = list(struct.iter_unpack('<dd', record[44 + 4 * parts:44 + 4 * parts + 16 * points]))
                stats[county] = geometry_stats([coords[a:b] for a, b in zip(starts, starts[1:])])
            require(not shp.read(1), 'Unexpected extra SHP records')
    return stats


def flatten_rings(geometry):
    if geometry['type'] == 'Polygon':
        return geometry['coordinates']
    require(geometry['type'] == 'MultiPolygon', 'Expected Polygon/MultiPolygon')
    return [ring for polygon in geometry['coordinates'] for ring in polygon]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', type=Path, default=ROOT.parent / 'QCEW_emplvl_byindustry_bymonth_mfg_county.csv')
    parser.add_argument('--geography', type=Path, default=ROOT.parent / 'tl_2020_us_county.zip')
    parser.add_argument('--output', type=Path, default=ROOT / 'manufacturing_map_data.js')
    parser.add_argument('--manifest', type=Path, default=ROOT / 'manufacturing_build_manifest.json')
    parser.add_argument('--interval', type=float, default=200)
    parser.add_argument('--expected-counties', type=int, default=3108)
    parser.add_argument('--expected-months', type=int, default=291)
    parser.add_argument('--expected-first-date', default='01/2002')
    parser.add_argument('--expected-latest-date', default='03/2026')
    args = parser.parse_args()
    require(args.interval > 0 and math.isfinite(args.interval), 'Simplification interval must be finite and positive')
    records, names, dates, counties, stats = read_csv(args.csv, args.expected_counties, args.expected_months)
    require(dates[0] == args.expected_first_date and dates[-1] == args.expected_latest_date, f'Unexpected date bounds: {dates[0]}–{dates[-1]}')
    print(f'Validated {len(records):,} rows, {len(counties):,} counties, {len(dates)} months', flush=True)
    source_stats = source_geometry(args.geography)
    require(all(c in source_stats for c in counties), 'CSV GEOIDs missing in 2020 geography')
    cli = ROOT / 'node_modules/mapshaper/bin/mapshaper'
    require(cli.exists(), 'Run npm ci before building')
    node = os.environ.get('NODE_BINARY') or shutil.which('node')
    require(node is not None, 'Node.js is required (or set NODE_BINARY)')
    with tempfile.TemporaryDirectory(prefix='county-build-') as temporary:
        geo_path = Path(temporary) / 'counties.json'
        command = [node, str(cli), '-i', str(args.geography.resolve()), '-proj', 'wgs84', '-simplify',
                   'weighted', 'variable', f'interval=Math.min({args.interval:g},Math.sqrt(Number(ALAND)+Number(AWATER))*0.01)', 'keep-shapes', 'stats',
                   '-o', str(geo_path), 'format=geojson', 'precision=0.00001']
        subprocess.run(command, check=True)
        features = json.loads(geo_path.read_text())['features']
    by_id = {}
    output_stats = {}
    for feature in features:
        county = feature['properties']['GEOID']
        require(county not in by_id, f'Duplicate output GEOID: {county}')
        if county not in names:
            continue
        geometry = feature['geometry']
        require(geometry is not None, f'County disappeared: {county}')
        rings = flatten_rings(geometry)
        for ring in rings:
            require(len(ring) >= 4 and ring[0] == ring[-1], f'Invalid/empty ring: {county}')
            require(all(len(p) == 2 and all(math.isfinite(v) for v in p) and -180 <= p[0] <= 180 and -90 <= p[1] <= 90 for p in ring), f'Invalid coordinates: {county}')
        output_stats[county] = geometry_stats(rings)
        require(output_stats[county]['area_m2'] > 0, f'Zero-area county: {county}')
        by_id[county] = {'type': 'Feature', 'properties': {'county_id': county, 'county_name': names[county]}, 'geometry': geometry}
    require(set(by_id) == set(counties), 'Geography join does not cover every included county')
    provenance = {
        'employmentSource': {'file': args.csv.name, 'sha256': digest(args.csv)},
        'geographySource': {'file': args.geography.name, 'sha256': digest(args.geography), 'vintage': 2020, 'join': 'cty_id = GEOID; no crosswalk', 'crs': 'EPSG:4326'},
        'scope': 'Counties present in the employment CSV; reported observations only',
        'units': 'Jobs; source numeric precision preserved',
        'missingSemantics': 'Blank source cells are null: Data missing. Explicit zero remains zero.',
        'geometry': {'algorithm': 'mapshaper weighted Visvalingam, shared topology, intersection repair, keep-shapes', 'maximumIntervalMetres': args.interval, 'intervalRule': 'min(maximumIntervalMetres, sqrt(ALAND + AWATER) * 0.01); shared arcs use the smaller adjoining interval', 'coordinatePrecisionDegrees': 0.00001, 'smallComponents': 'Sub-resolution islands/holes may collapse; every county is retained.'},
    }
    base = {'schemaVersion': 2, 'dates': dates, 'latestDate': dates[-1], 'metrics': METRICS, 'counties': [{'id': c, 'name': names[c]} for c in counties], 'geojson': {'type': 'FeatureCollection', 'features': [by_id[c] for c in counties]}, 'provenance': provenance}
    compact = lambda v: json.dumps(v, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    # Write to a sibling temporary file then replace: failed builds leave old output intact.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = args.output.with_suffix(args.output.suffix + '.tmp')
    with temp_output.open('w', encoding='utf-8', newline='\n') as f:
        f.write('window.MANUFACTURING_MAP_DATA=')
        f.write(compact(base)[:-1])
        f.write(',"values":[')
        for di, date in enumerate(dates):
            if di:
                f.write(',')
            f.write('[' + ','.join(records[(date, c)] for c in counties) + ']')
        f.write(']};\n')
    temp_output.replace(args.output)
    def totals(all_stats):
        return {key: sum(all_stats[c][key] for c in counties) for key in ('rings', 'vertices', 'area_m2')}
    changes = [{'id': c, 'name': names[c], 'relativeAreaChange': (output_stats[c]['area_m2'] / source_stats[c]['area_m2'] - 1), 'sourceRings': source_stats[c]['rings'], 'outputRings': output_stats[c]['rings']} for c in counties]
    manifest = {'schemaVersion': 2, 'builder': 'scripts/build_county_data.py', 'provenance': provenance,
                'dependencies': {'mapshaper': json.loads((ROOT / 'node_modules/mapshaper/package.json').read_text())['version'], 'python': '3.10+ standard library'},
                'source': {'rows': len(records), 'counties': len(counties), 'months': len(dates), 'firstDate': dates[0], 'latestDate': dates[-1], 'metrics': stats, 'geographyFeatures': len(source_stats), 'unusedGeographyFeatures': len(source_stats) - len(counties)},
                'geometry': {'sourceIncluded': totals(source_stats), 'output': totals(output_stats), 'areaMethod': 'Spherical ring area, radius 6378137m, signed exterior/hole sums; diagnostic approximation.', 'largestRelativeAreaChanges': sorted(changes, key=lambda c: abs(c['relativeAreaChange']), reverse=True)[:20], 'representativeCounties': [c for c in changes if c['id'] in {'11001','51510','51610','51700','25019','25007','06075','22071','12087','53055'}]},
                'output': {'file': args.output.name, 'bytes': args.output.stat().st_size, 'sha256': digest(args.output)}}
    args.manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
    print(f'Wrote {args.output.stat().st_size:,} bytes; {manifest["geometry"]["output"]["vertices"]:,} vertices', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, InvalidOperation, subprocess.CalledProcessError) as error:
        sys.exit(f'Build failed: {error}')
