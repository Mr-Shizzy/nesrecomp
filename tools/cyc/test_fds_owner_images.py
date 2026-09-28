#!/usr/bin/env python3
"""Parse every Famicom Disk System image in a local library (not run in CI).

Opens every .zip under --library recursively, read-only, and for each .fds/.qd
member (written only to --out, never next to the source):
  * NESRecomp --fds-info must exit 0: every side has disk info, a file amount,
    a clean file walk and no defect flag. Hidden files are reported, not failed.
  * the side count and file table must agree with an independent Python walk;
  * --fds-stream must equal the Mesen 0.9.9 and Mesen2 loader transliterations
    in tools/cyc/fds_fixtures.py, and every computed-CRC block must check;
  * the image with its fwNES header removed (or added, for raw images) must
    give the same file table and the same side streams.
Writes results.json to --out and prints a summary. No image is copied into
the repository.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import subprocess
import zipfile

from fds_fixtures import LEAD_IN, BLOCK_GAP, kermit, mesen099_stream, mesen2_stream


def run(argv):
    p = subprocess.run([str(a) for a in argv], capture_output=True, text=True, timeout=120,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return p.returncode, p.stdout + p.stderr


def info(recompiler, path):
    code, out = run([recompiler, '--fds-info', path])
    image = re.search(r'^IMAGE format=(\w+) sides=(\d+) side_bytes=(\d+) image_bytes=(\d+) flags=(\S+)$', out, re.M)
    files = re.findall(r'^FILE (\d+) (\d+) number=\$(\w\w) id=\$(\w\w) name=("(?:[^"\\]|\\.)*") load=\$(\w{4}) '
                       r'size=\$(\w{4}) type=(\d+)\(\w+\) header=\$(\w{4}) data=(\S+) hidden=(\w+)$', out, re.M)
    ends = re.findall(r'^END (\d+) files=(\d+) end=\$(\w+) end_code=\$(\w\w) flags=(\S+)$', out, re.M)
    sides = re.findall(r'^SIDE (\d+) game=("(?:[^"\\]|\\.)*") .* file_amount=(\d+)$', out, re.M)
    return code, out, image, files, ends, sides


def python_walk(data, offset, side, side_bytes, qd):
    """Independent file-table walk: [(name, load, size, type)], file amount."""
    b = data[offset + side * side_bytes: offset + (side + 1) * side_bytes]
    crc = 2 if qd else 0
    assert b[0] == 1 and b[1:15] == b'*NINTENDO-HVC*'
    j = 56 + crc
    assert b[j] == 2
    amount = b[j + 1]
    j += 2 + crc
    table = []
    while j < len(b) and b[j] == 3:
        h = b[j:j + 16]
        size = h[13] | h[14] << 8
        table.append((h[3:11], h[11] | h[12] << 8, size, h[15]))
        j += 16 + crc
        assert b[j] == 4
        j += 1 + size + crc
    return table, amount


def check_computed(stream):
    """Every block in a computed-CRC stream: $80, block, CRC with zero residue."""
    i, blocks, size = LEAD_IN, 0, 0
    while i < len(stream) and stream[i] == 0x80:
        code = stream[i + 1]
        n = {1: 56, 2: 2, 3: 16}.get(code, 1 + size if code == 4 else 0)
        assert n, (i, code)
        if code == 3:
            size = stream[i + 14] | stream[i + 15] << 8
        blk = stream[i:i + 1 + n + 2]
        assert kermit(blk) == 0, ('crc', i)
        i += 1 + n + 2 + BLOCK_GAP
        blocks += 1
    assert not any(stream[i:]), 'data after the last block'
    return blocks


def check_image(args, zpath, member, data, index):
    work = args.out / f'img{index:03d}'
    work.mkdir(parents=True, exist_ok=True)
    qd = member.lower().endswith('.qd')
    ext = '.qd' if qd else '.fds'
    path = work / ('image' + ext)
    path.write_bytes(data)
    code, out, image, files, ends, sides = info(args.recompiler, path)
    (work / 'info.txt').write_text(out, encoding='utf-8', newline='\n')
    result = dict(zip=str(zpath), member=member, bytes=len(data), ok=False)
    try:
        assert image, 'no IMAGE line'
        fmt, nsides, side_bytes = image[1], int(image[2]), int(image[3])
        result.update(format=fmt, sides=nsides, image_flags=image[5])
        assert code == 0, f'--fds-info exit {code}'
        headered = data[:4] == b'FDS\x1a'
        offset = 16 if headered else 0
        assert nsides == (data[4] if headered else len(data) // side_bytes) and len(ends) == nsides
        result['files'] = [int(e[1]) for e in ends]
        result['hidden'] = sum(f[10] == 'yes' for f in files)
        result['side_flags'] = [e[4] for e in ends]
        result['games'] = [s[1] for s in sides]
        for s in range(nsides):
            table, amount = python_walk(data, offset, s, side_bytes, qd)
            mine = [f for f in files if int(f[0]) == s]
            assert len(table) == len(mine) == int(ends[s][1]), ('file count', s)
            assert int(sides[s][2]) == amount
            for (name, load, size, ftype), f in zip(table, mine):
                assert (load, size, ftype) == (int(f[5], 16), int(f[6], 16), int(f[7])), ('file', s, f)
                assert f[9] != 'missing'
            assert sum(f[10] == 'yes' for f in mine) == max(0, len(table) - amount)
        # Headered <-> raw twin: same table, same streams. (.qd has no fwNES form.)
        twin = None
        if not qd:
            twin = work / ('twin' + ext)
            twin.write_bytes(data[16:] if headered else b'FDS\x1a' + bytes([nsides]) + bytes(11) + data)
            tcode, tout, timage, tfiles, tends, _ = info(args.recompiler, twin)
            assert tcode == 0 and tfiles == files and tends == ends, 'twin file table'
        streams = 0
        for s in range(nsides):
            for profile, build in (('mesen099', mesen099_stream), ('mesen2', mesen2_stream)):
                for crc in ('computed', 'mesen'):
                    got = {}
                    for label, src in (('image', path),) + ((('twin', twin),) if twin else ()):
                        out_bin = work / f'{label}.s{s}.{profile}.{crc}.bin'
                        c, o = run([args.recompiler, '--fds-stream', src, s, out_bin, '--fds-profile', profile, '--fds-crc', crc])
                        assert c == 0, o
                        got[label] = out_bin.read_bytes()
                        out_bin.unlink()
                    want = build(data, offset, s, side_bytes, qd, crc)
                    assert got['image'] == want, (profile, crc, s, 'differs from the Mesen transliteration')
                    assert got.get('twin', got['image']) == got['image'], (profile, crc, s, 'raw and headered streams differ')
                    if crc == 'computed' and profile == 'mesen099':
                        assert check_computed(got['image']) == 2 + 2 * len(python_walk(data, offset, s, side_bytes, qd)[0])
                    streams += 1
        result['streams'] = streams
        result['ok'] = True
    except AssertionError as e:
        result['error'] = repr(e)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--recompiler', type=Path, required=True)
    ap.add_argument('--library', type=Path, default=Path('//SERVER/Games/FDS'))
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--jobs', type=int, default=8)
    args = ap.parse_args()
    args.recompiler = args.recompiler.resolve()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    zips = sorted(p for p in args.library.rglob('*') if p.suffix.lower() == '.zip')
    tasks = []
    for z in zips:
        with zipfile.ZipFile(z) as zf:   # read-only
            for m in zf.infolist():
                if m.filename.lower().endswith(('.fds', '.qd')):
                    tasks.append((z, m.filename, zf.read(m)))
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(lambda t: check_image(args, t[0], t[1], t[2], t[3]),
                                [(z, m, d, i) for i, (z, m, d) in enumerate(tasks)]))
    (args.out / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8', newline='\n')
    ok = [r for r in results if r['ok']]
    by_format = {}
    for r in results:
        key = (r.get('format', '?'), r.get('sides', '?'))
        by_format[key] = by_format.get(key, 0) + 1
    print(f'{len(zips)} zips, {len(results)} images: {len(ok)} parse cleanly')
    for (fmt, sides), n in sorted(by_format.items(), key=str):
        print(f'  {fmt} {sides} side(s): {n}')
    print(f'  {sum(sum(r.get("files", [])) for r in ok)} files, {sum(r.get("hidden", 0) for r in ok)} hidden, '
          f'{sum(r.get("streams", 0) for r in ok)} side streams checked')
    flagged = [r for r in ok if r['image_flags'] != 'none' or any(f != 'none' for f in r['side_flags'])]
    for r in flagged:
        print(f'  note {r["member"]}: image {r["image_flags"]}, sides {r["side_flags"]}')
    for r in results:
        if not r['ok']:
            print(f'  FAIL {r["zip"]} :: {r["member"]}: {r.get("error")}')
    if len(ok) != len(results) or not zips:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
