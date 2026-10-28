#!/usr/bin/env python3
"""Clip bundled county geometry to land, removing Great Lakes and coastal water.

TIGER county polygons extend into the Great Lakes and coastal waters. This step
intersects each county with the Census cartographic-boundary state outline (which
is clipped to the shoreline) and rewrites only the geometry, provenance and
manifest. Employment values are copied byte-for-byte. Run after build_county_data.py.

Requires geopandas/shapely.
"""
import argparse
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
PRECISION = 1e-5
# Cartographic coastline is generalised at 1:500k; ~200 m (the builder maximum) keeps it smooth without adding many vertices.
LAND_SIMPLIFY_DEGREES = 0.002
# Water-side slivers left between TIGER and cartographic lines (about 0.01 km²).
MIN_PART_AREA_DEGREES = 1e-6


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def round_coords(value):
    if isinstance(value[0], (int, float)):
        return [round(value[0], 5), round(value[1], 5)]
    return [round_coords(child) for child in value]


def clean_parts(geometry):
    parts = [p for p in getattr(geometry, 'geoms', [geometry]) if p.geom_type == 'Polygon' and p.area >= MIN_PART_AREA_DEGREES]
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else shapely.MultiPolygon(parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=ROOT / 'manufacturing_map_data.js')
    parser.add_argument('--manifest', type=Path, default=ROOT / 'manufacturing_build_manifest.json')
    parser.add_argument('--land', type=Path, default=ROOT.parents[2] / 'Shape Files/cb_2018_us_state_500k.zip')
    args = parser.parse_args()

    text = args.bundle.read_text(encoding='utf-8')
    if not text.startswith(PREFIX) or VALUES_KEY not in text:
        sys.exit('Unexpected bundle layout; rebuild with build_county_data.py first')
    split = text.index(VALUES_KEY)
    head = json.loads(text[len(PREFIX):split] + '}')
    tail = text[split:]
    if 'landMask' in head['provenance']:
        sys.exit('Bundle is already clipped to land; rebuild before clipping again')

    states = {c['id'][:2] for c in head['counties']}
    land = geopandas.read_file(f'zip://{args.land.resolve()}')
    land = land[land['STATEFP'].isin(states)]
    # NAD83 and WGS84 differ by well under a metre here, matching the builder's treatment.
    land = shapely.union_all(land.geometry.values).simplify(LAND_SIMPLIFY_DEGREES, preserve_topology=True)
    shapely.prepare(land)

    clipped = vertices_before = vertices_after = 0
    for feature in head['geojson']['features']:
        county = feature['properties']['county_id']
        geometry = shape(feature['geometry'])
        vertices_before += int(shapely.get_num_coordinates(geometry))
        if not land.contains_properly(geometry):
            if not geometry.is_valid:
                geometry = shapely.make_valid(geometry)
            local_land = shapely.clip_by_rect(land, *geometry.bounds)
            result = shapely.set_precision(geometry.intersection(local_land), PRECISION)
            result = clean_parts(result)
            if result is None:
                sys.exit(f'Clip failed: county {county} has no land area')
            feature['geometry'] = {'type': result.geom_type, 'coordinates': round_coords(mapping(result)['coordinates'])}
            clipped += 1
        vertices_after += int(shapely.get_num_coordinates(shape(feature["geometry"])))

    head['provenance']['landMask'] = {
        'file': args.land.name, 'sha256': digest(args.land), 'vintage': 2018,
        'rule': 'County geometry intersected with the cartographic state outline (shoreline-clipped) to remove Great Lakes and coastal water',
        'simplifyDegrees': LAND_SIMPLIFY_DEGREES, 'minimumPartAreaDegrees2': MIN_PART_AREA_DEGREES,
    }
    compact = json.dumps(head, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    temp_output = args.bundle.with_suffix(args.bundle.suffix + '.tmp')
    with temp_output.open('w', encoding='utf-8', newline='\n') as f:
        f.write(PREFIX)
        f.write(compact[:-1])
        f.write(tail)
    temp_output.replace(args.bundle)

    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    manifest['provenance'] = head['provenance']
    manifest['landClip'] = {'script': 'scripts/clip_to_land.py', 'countiesClipped': clipped,
                            'verticesBefore': vertices_before, 'verticesAfter': vertices_after}
    manifest['output'] = {'file': args.bundle.name, 'bytes': args.bundle.stat().st_size, 'sha256': digest(args.bundle)}
    args.manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
    print(f'Clipped {clipped:,} counties to land; {vertices_before:,} -> {vertices_after:,} vertices; '
          f'{args.bundle.stat().st_size:,} bytes', flush=True)


if __name__ == '__main__':
    main()
