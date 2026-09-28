#!/usr/bin/env python3
"""Native / --interp-only / standalone-interpreter parity of an FDS program on
owner images (local check, not CI: needs the BIOS and disk images).

Runs a compiled FDS program (its BIOS recompiled, disk code interpreted), the
same program with --interp-only, and cyc_interp, at all four CPU/PPU
alignments, and requires identical --hash-out traces (every bus access of
every cycle, memories, picture, and the hardware state including the drive).

  python tools/cyc/test_fds_owner_parity.py --exe build/fds-smb2j/nes_game.exe \\
      --interp build/fds-cyc/cyc_interp.exe --bios bios/disksys.rom \\
      --case smb2j,800,smb2j.fds --case nodisk,300,smb2j.fds,--fds-boot-disk,none \\
      --case otocky,3000,otocky.fds,--fds-event,1199:eject,--fds-event,1258:insert=1 --out out/
"""
import argparse
from pathlib import Path
import subprocess
import sys

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def run(cmd, log):
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=3600, creationflags=NO_WINDOW)
    Path(log).write_text(' '.join(str(c) for c in cmd) + '\n' + p.stdout + p.stderr)
    if p.returncode:
        raise RuntimeError(f'exit {p.returncode}; see {log}')
    return p.stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--exe', type=Path, required=True)
    ap.add_argument('--interp', type=Path, required=True)
    ap.add_argument('--bios', type=Path, required=True)
    ap.add_argument('--case', action='append', required=True, help='NAME,FRAMES,IMAGE[,ARG...]')
    ap.add_argument('--align', type=int, nargs='*', default=[0, 1, 2, 3])
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    failed = 0
    for spec in args.case:
        name, frames, image, *extra = spec.split(',')
        for align in args.align:
            traces = {}
            for mode, exe, more in [('native', args.exe, []), ('interp', args.exe, ['--interp-only']),
                                    ('standalone', args.interp, [])]:
                trace = out / f'{name}_a{align}_{mode}.txt'
                stdout = run([exe, image, '--fds-bios', args.bios, '--frames', frames, '--align', align,
                              '--hash-out', trace] + extra + more, trace.with_suffix('.log'))
                traces[mode] = trace.read_text().splitlines()
                if mode == 'native':
                    summary = next(l for l in stdout.splitlines() if l.startswith('mode='))
            bad = []
            for mode in ('interp', 'standalone'):
                a, b = traces['native'], traces[mode]
                diff = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), None)
                if diff is not None or len(a) != len(b):
                    bad.append(f'{mode} differs from frame {diff if diff is not None else min(len(a), len(b))}')
            status = 'identical' if not bad else '; '.join(bad)
            print(f'{name} align {align}: {len(traces["native"])} frames, native/interp/standalone {status} ({summary})')
            failed += bool(bad)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
