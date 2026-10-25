#!/usr/bin/env python3
"""Copy pinned Leaflet browser assets locally after npm ci."""
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'node_modules/leaflet'
DESTINATION = ROOT / 'vendor/leaflet'
EXPECTED_VERSION = '1.9.4'


def main():
    package = json.loads((SOURCE / 'package.json').read_text())
    if package['version'] != EXPECTED_VERSION:
        raise ValueError(f'Expected Leaflet {EXPECTED_VERSION}; run npm ci')
    assets = ['leaflet.js', 'leaflet.js.map', 'leaflet.css']
    assets += [str(p.relative_to(SOURCE / 'dist')) for p in sorted((SOURCE / 'dist/images').glob('*')) if p.is_file()]
    for asset in assets:
        destination = DESTINATION / asset
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / 'dist' / asset, destination)
    shutil.copyfile(SOURCE / 'LICENSE', DESTINATION / 'LICENSE')
    # The CSS image paths and development source map must remain usable locally.
    css = (DESTINATION / 'leaflet.css').read_text()
    for reference in re.findall(r'url\([\'"]?([^\)\'\"]+)', css):
        if not reference.startswith('#') and not (DESTINATION / reference).is_file():
            raise ValueError(f'Missing CSS asset: {reference}')
    js = (DESTINATION / 'leaflet.js').read_text()
    for reference in re.findall(r'sourceMappingURL=(\S+)', js):
        if not (DESTINATION / reference).is_file():
            raise ValueError(f'Missing JavaScript source map: {reference}')
    for asset in assets:
        expected = hashlib.sha256((SOURCE / 'dist' / asset).read_bytes()).digest()
        actual = hashlib.sha256((DESTINATION / asset).read_bytes()).digest()
        if actual != expected:
            raise ValueError(f'Copy checksum mismatch: {asset}')
    print(f'Vendored Leaflet {EXPECTED_VERSION}: {len(assets) + 1} files, CSS references and checksums verified')


if __name__ == '__main__':
    main()
