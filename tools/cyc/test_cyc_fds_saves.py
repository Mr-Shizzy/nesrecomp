#!/usr/bin/env python3
"""FDS disk saves across processes (CTest cyc_fds_saves; no owner data).

Runs tools/cyc/fds_save_fixtures.py's saving program on a cycle host
(cyc_interp, or a compiled program) and checks, process after process:

  round trip   each run reads the counter the last run saved and saves it + 1;
               the save holds side 0 only, whole, and its stream is what the
               program wrote: the gap zeros, the $80 mark, block 4, and a CRC
               equal to CRC-16/KERMIT over mark + block (the program's pass 3
               reads it back with the CRC checked); block 3's CRC before it is
               intact; every byte outside the write run is the image's
  the image    disk.fds's SHA-1 never changes; no temporary file is left
  identity     the save of disk.fds works for its raw twin (same disk); a save
               for other.fds, a corrupt, truncated or newer-version save is
               refused (exit 2) and left byte for byte as it was
  cadence      the save is written when the drive stops (ring fds.save "idle"),
               or at exit when the run ends first ("exit"); a run that writes
               nothing rewrites nothing
  protect      --fds-write-protect: the program sees the tab, writes nothing
  Mesen        --fds-export-ips gives an IPS of the image whose patched file
               holds the saved data; --fds-import-ips of it starts from that
               disk; a malformed .ips is refused
  position     the Mesen two-behind write position (--fds-write-at mesen)
               clobbers block 3's CRC: pass 3 with CRC checks sees it

  python tools/cyc/test_cyc_fds_saves.py --host build/cyc/cyc_interp.exe --out build/cyc-fds-saves
  (tools/cyc/test_cyc_fds_runtime.py also compiles the program and runs this
  with the native build and with --interp-only)
"""
import argparse
import hashlib
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import zlib

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fds_fixtures import kermit  # noqa: E402

checks = 0


def check(cond, what):
    global checks
    checks += 1
    if not cond:
        raise AssertionError(what)


def parse_save(data):
    """Independent reader of the disk save format (common/nes_fds_save.h)."""
    assert data[:8] == b'NRFDSAV\x1a', 'magic'
    assert struct.unpack_from('<I', data, len(data) - 4)[0] == zlib.crc32(data[:-4]), 'file CRC'
    version, crc, fnv_lo, fnv_hi, nbytes, sides, side_bytes = struct.unpack_from('<IIIIIHH', data, 8)
    count = data[35]
    p, out = 40, {}
    for _ in range(count):
        side, length, scrc = data[p], *struct.unpack_from('<II', data, p + 4)
        stream = data[p + 12:p + 12 + length]
        assert zlib.crc32(stream) == scrc, 'side CRC'
        out[side] = stream
        p += 12 + length
    assert p == len(data) - 4
    return {'version': version, 'crc': crc, 'fnv': fnv_lo | fnv_hi << 32, 'bytes': nbytes, 'sides': sides,
            'side_bytes': side_bytes, 'layout': data[32], 'crc_mode': data[33], 'write_at': data[34], 'streams': out}


def ips_apply(ips, data):
    assert ips[:5] == b'PATCH'
    out, p = bytearray(data), 5
    while ips[p:p + 3] != b'EOF':
        addr, n = int.from_bytes(ips[p:p + 3], 'big'), int.from_bytes(ips[p + 3:p + 5], 'big')
        p += 5
        if n:
            chunk, p = ips[p:p + n], p + n
        else:
            chunk, p = bytes([ips[p + 2]]) * int.from_bytes(ips[p:p + 2], 'big'), p + 3
        out[addr:addr + len(chunk)] = chunk
    return bytes(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--host', required=True, type=Path, help='cyc_interp or a compiled FDS program')
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--align', type=int, nargs='*', default=[0, 1, 2, 3])
    ap.add_argument('--host-args', default='', help='extra host arguments for every run (e.g. --interp-only)')
    args = ap.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(HERE / 'fds_save_fixtures.py'), '--out', str(out)], check=True,
                   capture_output=True, creationflags=NO_WINDOW)
    exp = {}
    for line in (out / 'expect.txt').read_text().splitlines():
        k, *v = line.split()
        exp[k] = v
    gap, data_len = int(exp['gap'][0]), int(exp['data'][0])
    marks = [int(x) for x in exp['marks']]
    file1_first, initial = int(exp['file1_first'][0]), bytes.fromhex(exp['initial'][0])
    disk = out / 'disk.fds'
    image_sha = hashlib.sha1(disk.read_bytes()).hexdigest()
    host = args.host.resolve()
    bios = out / 'bios' / 'disksys.rom'
    runs = 0

    def run(name, image, extra, ok=True, frames=200):
        nonlocal runs
        runs += 1
        mem, ring = out / f'{name}.mem', out / f'{name}.ring'
        p = subprocess.run([str(host), str(image), '--fds-bios', str(bios), '--frames', str(frames),
                            '--mem-frame', str(frames - 1), '--mem-out', str(mem), '--ring-out', str(ring)] +
                           args.host_args.split() + [str(x) for x in extra], cwd=out, capture_output=True, text=True, timeout=300,
                           creationflags=NO_WINDOW)
        (out / f'{name}.log').write_text(p.stdout + p.stderr)
        check((p.returncode == 0) == ok, f'{name}: exit {p.returncode} (expected {"0" if ok else "2"}); see {name}.log')
        if not ok:
            check(p.returncode == 2, f'{name}: refused with exit {p.returncode}')
            return None, p
        ram = bytearray(0x800)
        for line in mem.read_text().splitlines():
            if line.startswith('ram '):
                a, rest = line[4:].split(':')
                for i, b in enumerate(rest.split()):
                    ram[int(a, 16) + i] = int(b, 16)
        return {'ram': ram, 'ring': ring.read_text(), 'log': p.stdout + p.stderr}, p

    def results(r):
        m = r['ram']
        return {'old': m[0x410], 'new': m[0x411], 'crc3': m[0x412], 'same': m[0x413], 'file1': m[0x414],
                'protect': m[0x415], 'crc12': m[0x416], 'done': m[0x420]}

    for align in args.align:
        a = ['--align', align]
        save = out / f'a{align}.fdssave'
        save.unlink(missing_ok=True)
        # --- round trip, three processes
        prev = None
        for n in range(3):
            r, _ = run(f'a{align}_rt{n}', disk, a + ['--fds-crc-check', '--save-file', save])
            res = results(r)
            check(res == {'old': n, 'new': n + 1, 'crc3': 0, 'same': 1, 'file1': file1_first, 'protect': 0,
                          'crc12': 0, 'done': 0xC3}, f'align {align} run {n}: {res}')
            check(r['ring'].count(' fds.save saved (idle) sides=1 ') == 1 and r['ring'].count(' fds.save ') == 1,
                  f'align {align} run {n}: not exactly one (idle) save in the ring')
            check((' fds.load save-file sides=1 ' in r['ring']) == (n > 0), f'align {align} run {n}: fds.load')
            s = parse_save(save.read_bytes())
            check(sorted(s['streams']) == [0] and s['sides'] == 2 and s['side_bytes'] == 65500 and s['version'] == 1,
                  f'align {align} run {n}: save holds {sorted(s["streams"])}')
            st = s['streams'][0]
            # the block the program wrote, whole: gap, mark, code, data, CRC
            w = next(l for l in r['ring'].splitlines() if ' fds.wblock ' in l)
            mark = int(w.split('mark=')[1].split()[0])
            wrun = next(l for l in r['ring'].splitlines() if ' fds.wrun ' in l)
            if n == 0:
                first_change = int(wrun.split()[1])   # the frame the write run ended in
            start = int(wrun.split('from=')[1].split()[0])
            stored = int(wrun.split('stored=')[1].split()[0])
            block3_end = marks[2] + 1 + 16 + 2
            check(start == block3_end, f'write run starts at {start}, not on the byte after block 3 ({block3_end})')
            check(mark == start + gap, f'mark at {mark}, expected {start + gap}')
            check(st[start:mark] == bytes(gap) and st[mark] == 0x80 and st[mark + 1] == 4,
                  'gap zeros, $80 mark, block code 4 as written')
            block = st[mark + 1:mark + 2 + data_len]
            want = bytes((n + 1 + 0x11 * i) & 0xFF for i in range(data_len))
            check(block[1:] == want, f'saved data {block[1:].hex()} != {want.hex()}')
            crc = st[mark + 2 + data_len] | st[mark + 3 + data_len] << 8
            check(crc == kermit(b'\x80' + block), f'written CRC {crc:04X} != CRC-16 of mark + block')
            b3 = marks[2]
            check(st[b3 + 17] | st[b3 + 18] << 8 == kermit(st[b3:b3 + 17]), 'block 3 CRC intact')
            if prev is not None:
                diff = [i for i in range(len(st)) if st[i] != prev[i]]
                check(diff and min(diff) >= start and max(diff) < start + stored,
                      f'bytes changed outside the write run: {diff[:4]}..')
            prev = st
            check(hashlib.sha1(disk.read_bytes()).hexdigest() == image_sha, 'the image changed')
        good = save.read_bytes()
        # --- a run that writes nothing (protected) rewrites nothing; the program sees the tab
        r, _ = run(f'a{align}_protect', disk, a + ['--fds-crc-check', '--save-file', save, '--fds-write-protect'])
        res = results(r)
        check(res['protect'] == 4 and res['old'] == 3 and res['new'] == 3 and res['done'] == 0xC3, f'protect: {res}')
        check(save.read_bytes() == good and ' fds.save ' not in r['ring'], 'protected run saved')
        # --- a save that cannot be written: the run fails, the old file stays whole, no temporary file
        os.chmod(save, stat.S_IREAD)
        try:
            r, p = run(f'a{align}_readonly', disk, a + ['--save-file', save], ok=False)
            check(save.read_bytes() == good and 'previous file retained' in p.stderr, 'read-only save')
            # idle save (~frame 127) fails, one retry an idle period later (~187), then at exit (200)
            check(p.stderr.count('cannot write disk save') == 3, 'retries per idle period, not per frame')
        finally:
            os.chmod(save, stat.S_IREAD | stat.S_IWRITE)
        # --- the raw twin is the same disk
        r, _ = run(f'a{align}_raw', out / 'disk_raw.fds', a + ['--save-file', save])
        check(results(r)['old'] == 3 and results(r)['new'] == 4, f'raw twin: {results(r)}')
        good = save.read_bytes()
        # --- refused saves stay exactly as they are
        bad = {'foreign': None, 'flipped': bytearray(good), 'truncated': good[:-1], 'short': good[:30],
               'newer': bytearray(good), 'empty': b''}
        bad['flipped'][50000] ^= 0x40
        bad['newer'][8] = 2
        bad['newer'][-4:] = struct.pack('<I', zlib.crc32(bytes(bad['newer'][:-4])))
        for kind, content in bad.items():
            victim = out / f'a{align}_{kind}.fdssave'
            if kind == 'foreign':
                victim.unlink(missing_ok=True)
                run(f'a{align}_other', out / 'other.fds', a + ['--save-file', victim])
                content = victim.read_bytes()
                image = disk
            else:
                victim.write_bytes(bytes(content))
                image = disk
            _, p = run(f'a{align}_{kind}', image, a + ['--save-file', victim], ok=False)
            check(victim.read_bytes() == bytes(content), f'{kind} save was modified')
            want = 'different disk' if kind == 'foreign' else 'newer version' if kind == 'newer' else 'corrupt'
            check(want in p.stderr, f'{kind}: message {p.stderr.strip()!r}')
        # --- cadence: a run that ends right after the write saves at exit
        early = out / f'a{align}_early.fdssave'
        early.unlink(missing_ok=True)
        r, _ = run(f'a{align}_early', disk, a + ['--save-file', early], frames=60)
        check(' fds.save saved (exit) sides=1 ' in r['ring'] and ' (idle)' not in r['ring'], 'save at exit')
        w = next(l for l in r['ring'].splitlines() if ' fds.wblock ' in l)
        check(parse_save(early.read_bytes())['streams'][0][int(w.split('mark=')[1].split()[0])] == 0x80,
              'the exit save holds the block')
        # ... and ejecting the disk saves at once (the program has stopped the motor by frame 70)
        ej = out / f'a{align}_eject.fdssave'
        ej.unlink(missing_ok=True)
        r, _ = run(f'a{align}_eject', disk, a + ['--save-file', ej, '--fds-event', '70:eject'], frames=100)
        check(' fds.save saved (eject) sides=1 ' in r['ring'] and r['ring'].count(' fds.save ') == 1, 'save on eject')
        ev = [l for l in r['ring'].splitlines() if ' fds.save ' in l or ' fds.side ejected' in l]
        check(len(ev) == 2 and int(ev[0].split()[1]) == int(ev[1].split()[1]) == 70, f'eject and save at frame 70: {ev}')
        # --- Mesen: export, then start a fresh process from the export
        ips = out / f'a{align}.ips'
        ips.unlink(missing_ok=True)
        r, _ = run(f'a{align}_export', disk, a + ['--fds-export-ips', ips])
        patched = ips_apply(ips.read_bytes(), disk.read_bytes())
        off = patched.find(b'\x04' + bytes([1]) + bytes((1 + 0x11 * i) & 0xFF for i in range(1, data_len)))
        check(off > 0 and patched[:off].count(b'SAVEDATA') == 1, 'the exported .ips holds the saved data')
        check(len(patched) == len(disk.read_bytes()), 'the .ips keeps the image size')
        r, _ = run(f'a{align}_import', disk, a + ['--fds-import-ips', ips, '--fds-crc-check'])
        res = results(r)
        check(res['old'] == 1 and res['new'] == 2 and res['crc3'] == 0 and res['same'] == 1, f'import: {res}')
        check(' fds.load mesen-ips sides=1 ' in r['ring'], 'import ring event')
        garbage = out / f'a{align}_garbage.ips'
        garbage.write_bytes(b'PATCH\x00\x00')
        run(f'a{align}_garbage', disk, a + ['--fds-import-ips', garbage], ok=False)
        # --- the image path can never be a save or an export
        run(f'a{align}_self', disk, a + ['--save-file', disk], ok=False)
        run(f'a{align}_self_ips', disk, a + ['--fds-export-ips', disk], ok=False)
        check(hashlib.sha1(disk.read_bytes()).hexdigest() == image_sha, 'the image changed')
        # --- a drive that never stops: the save waits at most 3600 frames after the first change
        sp = out / f'a{align}_spin.fdssave'
        sp.unlink(missing_ok=True)
        r, _ = run(f'a{align}_spin', out / 'spin.fds', a + ['--save-file', sp], frames=3800)
        ev = [l for l in r['ring'].splitlines() if ' fds.save ' in l]
        # spin.fds runs as disk.fds does up to its write (run 0 above), then never stops the motor
        check(len(ev) == 1 and '(timeout)' in ev[0] and 0 <= int(ev[0].split()[1]) - first_change - 3600 <= 2,
              f'timeout save: {ev}, first change at frame {first_change}')
        # --- write position: Mesen's two-behind clobbers block 3's CRC
        r, _ = run(f'a{align}_mesenpos', disk, a + ['--fds-crc-check', '--fds-write-at', 'mesen'])
        res = results(r)
        check(res['crc3'] == 0x10 and res['same'] == 1, f'two-behind: {res}')
        r, _ = run(f'a{align}_mesenpos_nocheck', disk, a + ['--fds-write-at', 'mesen'])
        check(results(r)['crc3'] == 0, 'two-behind is invisible without CRC checks (Mesen)')
        print(f'align {align}: round trip x3, protect, raw twin, 6 refused saves, exit cadence, Mesen export/import, '
              'write position')
    check(not list(out.glob('*.tmp-*')), 'temporary save files left behind')
    print(f'cyc_fds_saves: {checks} checks over {runs} runs passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
