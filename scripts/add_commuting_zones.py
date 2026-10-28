#!/usr/bin/env python3
"""Add 1990 commuting zones (CZs) to the bundle: county membership, names, borders.

Each county gets its CZ from data/county_cz1990.csv. CZ borders are drawn by
merging the bundled (land-clipped) county shapes within each CZ, so they line up
exactly with county borders; only borders between CZs are kept, not coastlines.
The cz1990 shapefile is used as a check: every county must lie inside its CZ.
Employment values are copied byte-for-byte. Run after clip_to_land.py.

Requires geopandas/shapely.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

import geopandas
import shapely
from shapely.geometry import mapping, shape

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'window.MANUFACTURING_MAP_DATA='
VALUES_KEY = ',"values":['
GRID = 1e-5
# About 100 m: under one screen pixel even at the map's maximum zoom (~115-150 m/pixel).
BORDER_SIMPLIFY_DEGREES = 0.001


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def round_coords(value):
    if isinstance(value[0], (int, float)):
        return [round(value[0], 5), round(value[1], 5)]
    return [round_coords(child) for child in value]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=ROOT / 'manufacturing_map_data.js')
    parser.add_argument('--manifest', type=Path, default=ROOT / 'manufacturing_build_manifest.json')
    parser.add_argument('--crosswalk', type=Path, default=ROOT / 'data/county_cz1990.csv')
    parser.add_argument('--cz-shapes', type=Path, default=ROOT.parents[3] / 'Data/Crosswalks/raw_data/region/Shape Files/cz1990/cz1990.shp')
    args = parser.parse_args()

    text = args.bundle.read_text(encoding='utf-8')
    if not text.startswith(PREFIX) or VALUES_KEY not in text:
        sys.exit('Unexpected bundle layout; rebuild with build_county_data.py first')
    split = text.index(VALUES_KEY)
    head = json.loads(text[len(PREFIX):split] + '}')
    tail = text[split:]
    if 'commutingZones' in head['provenance']:
        sys.exit('Bundle already has commuting zones; rebuild before adding them again')
    if 'landMask' not in head['provenance']:
        sys.exit('Run clip_to_land.py first so CZ borders follow the shoreline')

    with args.crosswalk.open(newline='', encoding='utf-8') as f:
        rows = {row['cty_id']: row for row in csv.DictReader(f)}
    ids = [c['id'] for c in head['counties']]
    missing = [c for c in ids if c not in rows]
    if missing:
        sys.exit(f'Counties without a CZ: {missing[:10]}')
    cz_names = {}
    for county in ids:
        cz, name = rows[county]['cz_id'], rows[county]['cz_name']
        if cz_names.setdefault(cz, name) != name:
            sys.exit(f'CZ {cz} has more than one name')
    for county in head['counties']:
        county['cz'] = rows[county['id']]['cz_id']

    counties = geopandas.GeoDataFrame(
        {'cz': [rows[f['properties']['county_id']]['cz_id'] for f in head['geojson']['features']]},
        geometry=[shapely.make_valid(shape(f['geometry'])) for f in head['geojson']['features']], crs=4326)

    # Check against the cz1990 polygons: every county's interior point falls in its own CZ.
    cz_shapes = geopandas.read_file(args.cz_shapes).to_crs(4326)
    points = counties.copy()
    points['geometry'] = counties.representative_point()
    joined = geopandas.sjoin(points, cz_shapes, how='left', predicate='within')
    joined = joined[~joined.index.duplicated()]
    mismatched = (joined['cz_left'] != joined['cz_right'].astype('Int64').astype(str)).sum()
    if mismatched:
        sys.exit(f'{mismatched} counties fall outside their cz1990 polygon')

    zones = counties.dissolve('cz', method='coverage')
    all_borders = shapely.union_all(shapely.boundary(zones.geometry.values), grid_size=GRID)
    outline = shapely.boundary(shapely.union_all(zones.geometry.values, grid_size=GRID))
    internal = shapely.line_merge(shapely.difference(all_borders, outline.buffer(GRID), grid_size=GRID))
    internal = shapely.simplify(internal, BORDER_SIMPLIFY_DEGREES)
    head['czNames'] = dict(sorted(cz_names.items()))
    head['czBorders'] = {'type': 'Feature', 'properties': {},
                         'geometry': {'type': internal.geom_type, 'coordinates': round_coords(mapping(internal)['coordinates'])}}
    head['provenance']['commutingZones'] = {
        'vintage': 1990, 'crosswalk': {'file': args.crosswalk.name, 'sha256': digest(args.crosswalk)},
        'checkedAgainst': {'file': args.cz_shapes.name, 'sha256': digest(args.cz_shapes)},
        'borders': 'Bundled county shapes merged by CZ; only borders between CZs are drawn',
        'borderSimplifyDegrees': BORDER_SIMPLIFY_DEGREES,
    }

    compact = json.dumps(head, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    temp_output = args.bundle.with_suffix(args.bundle.suffix + '.tmp')
    with temp_output.open('w', encoding='utf-8', newline='\n') as f:
        f.write(PREFIX)
        f.write(compact[:-1])
        f.write(tail)
    temp_output.replace(args.bundle)

    vertices = int(shapely.get_num_coordinates(internal))
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    manifest['provenance'] = head['provenance']
    manifest['commutingZones'] = {'script': 'scripts/add_commuting_zones.py', 'zones': len(cz_names), 'borderVertices': vertices}
    manifest['output'] = {'file': args.bundle.name, 'bytes': args.bundle.stat().st_size, 'sha256': digest(args.bundle)}
    args.manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
    print(f'Added {len(cz_names):,} commuting zones; {vertices:,} border vertices; {args.bundle.stat().st_size:,} bytes', flush=True)


if __name__ == '__main__':
    main()
