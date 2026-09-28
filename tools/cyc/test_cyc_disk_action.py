#!/usr/bin/env python3
"""The player's Disk action on the whole machine (CTest cyc_disk_action_run).

Runs the standalone cyc_interp on the synthetic FDS program and disks of
tools/cyc/fds_hle_fixtures.py (no owner data), pressing the Disk action from
--input (`F DISK_ACTION`; the toast's clock is emulated time headless):

  swap         a peek then a press while the toast is up swaps disk 1 side A
               for side B: the program that waits for the player (scenario 2,
               "watch") sees one eject, a drive empty for the hold (30 frames),
               the insert, and loads side B
  same path    that run is byte-identical (--hash-out, --mem-out, --frame-log,
               --screenshot, ring) to the same eject and insert given as
               --fds-event: the action changes the drive only through the
               normal drive path, and the toast is never in any of them
  peek         a single press, or two presses further apart than the toast
               lasts, change nothing: hash-identical to a run without presses
  cycling      on a 4-sided (two-disk) image every press while the toast is up
               moves one side on and wraps (1, 2, 3, 0); a press during the hold
               moves the target on without a second eject
  toast        --present-out (what a window presents) shows the toast while it
               is up and equals the picture (--shot-every) before the first
               press and after the toast times out; the pictures of the action
               run equal those of the --fds-event run frame for frame
  alignments   the swap result and the path identity at CPU/PPU alignment 0-3

python tools/cyc/test_cyc_disk_action.py --interp build/cyc/cyc_interp.exe --out build/cyc-disk-action
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
FRAMES = 420
HOLD = 30                      # cyc_disk_action.h CYC_DISK_HOLD_FRAMES
TOAST_FRAMES = 3000 * 60.0988 / 1000   # CYC_DISK_TOAST_MS in emulated frames (~180.3)
checks = 0


def check(cond, what):
    global checks
    checks += 1
    if not cond:
        raise AssertionError(what)


def run(cmd, cwd, log):
    e = {k: v for k, v in os.environ.items() if k != 'NESRECOMP_FDS_HLE'}
    p = subprocess.run([str(x) for x in cmd], cwd=cwd, capture_output=True, text=True, timeout=900, env=e,
                       creationflags=NO_WINDOW)
    Path(log).write_text(' '.join(str(x) for x in cmd) + '\n' + p.stdout + p.stderr)
    if p.returncode:
        raise RuntimeError(f'exit {p.returncode}: {cmd}; see {log}')
    return p.stdout


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Run:
    def __init__(self, exe, image, bios, tag, out, inputs=(), events=(), align=0, shots=0, present=False):
        self.tag = tag
        d = out / tag
        d.mkdir(parents=True, exist_ok=True)
        self.dir = d
        cmd = [exe, image, '--fds-bios', bios, '--frames', FRAMES, '--align', align,
               '--hash-out', d / 'hash.txt', '--mem-frame', FRAMES - 1, '--mem-out', d / 'mem.txt',
               '--frame-log', d / 'frames.bin', '--ring-out', d / 'ring.txt', '--screenshot', d / 'shot.png']
        if shots:
            cmd += ['--shot-every', shots]
        if present:
            cmd += ['--present-out', d / 'present.png', '--present-every', shots]
        if inputs:
            (d / 'input.txt').write_text(''.join(f'{f} DISK_ACTION\n' for f in inputs), newline='\n')
            cmd += ['--input', d / 'input.txt']
        for ev in events:
            cmd += ['--fds-event', ev]
        self.stdout = run(cmd, out, d / 'run.log')
        self.ram = []
        for line in open(d / 'mem.txt'):
            if line.startswith('ram '):
                self.ram += [int(x, 16) for x in line.split()[2:]]
        self.sides = []
        for line in open(d / 'ring.txt'):
            parts = line.split(None, 4)
            if len(parts) >= 5 and parts[3] == 'fds.side':
                self.sides.append((int(parts[1]), parts[4].strip()))

    def files(self):
        return {n: digest(self.dir / n) for n in ('hash.txt', 'mem.txt', 'frames.bin', 'shot.png')}

    def presses(self):
        return re.findall(r'f=(\d+) disk action: ([a-z ]+) \(side (-?\d+), target (-?\d+)\)', self.stdout)

    def changes(self):
        """(frame, 'E'|'I', side) from the host's drive log lines"""
        out = []
        for f, what, side in re.findall(r'\[cyc disk\] f=(\d+) (eject|insert) side (\d+)', self.stdout):
            out.append((int(f), 'E' if what == 'eject' else 'I', int(side)))
        return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--interp', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    fx = out / 'fx'
    subprocess.run([sys.executable, str(HERE / 'fds_hle_fixtures.py'), '--out', str(fx)], check=True,
                   capture_output=True, creationflags=NO_WINDOW)
    exe, bios = args.interp.resolve(), fx / 'bios' / 'hle.rom'
    disk2, disk4 = fx / 'disk2_2.fds', fx / 'disk4_7.fds'

    for align in range(4):
        base = Run(exe, disk2, bios, f'none_a{align}', out, align=align)
        # peek at 60, press again at 70 while the toast is up: eject at 70, side B in at 70 + HOLD
        act = Run(exe, disk2, bios, f'action_a{align}', out, inputs=(60, 70), align=align, shots=10, present=True)
        check([p[1] for p in act.presses()] == ['peek', 'swap'], f'a{align}: presses {act.presses()}')
        check(act.changes() == [(70, 'E', 0), (70 + HOLD, 'I', 1)], f'a{align}: drive changes {act.changes()}')
        ram = act.ram
        check(ram[0x412] == 0x11 and ram[0x413] == 0 and ram[0x416] == 1 and ram[0x420] == 0xC3,
              f'a{align}: the watching program did not load side B: t={ram[0x412]:02X} ej={ram[0x416]}')
        check(ram[0x417] == HOLD, f'a{align}: drive empty {ram[0x417]} frames, want {HOLD}')
        check(base.ram[0x412] == 0x10 and base.ram[0x420] == 0, f'a{align}: without presses the program moved on')
        # the same eject and insert as disk events: byte-identical everywhere
        ev = Run(exe, disk2, bios, f'events_a{align}', out, events=('70:eject', f'{70 + HOLD}:insert=1'),
                 align=align, shots=10)
        check(act.files() == ev.files(), f'a{align}: the action run differs from the --fds-event run')
        check(act.sides == ev.sides, f'a{align}: ring side events differ {act.sides} vs {ev.sides}')
        for f in range(0, FRAMES, 10):
            name = f'shot_{f:05d}.png'
            check(digest(act.dir / name) == digest(ev.dir / name), f'a{align}: picture {name} differs')
        # peek only; two presses further apart than the toast: nothing changes
        peek = Run(exe, disk2, bios, f'peek_a{align}', out, inputs=(60,), align=align)
        check(peek.files()['hash.txt'] == base.files()['hash.txt'] and not peek.changes(), f'a{align}: a peek changed the run')
        late = int(60 + TOAST_FRAMES) + 2
        apart = Run(exe, disk2, bios, f'apart_a{align}', out, inputs=(60, late), align=align)
        check([p[1] for p in apart.presses()] == ['peek', 'peek'], f'a{align}: presses {apart.presses()}')
        check(apart.files()['hash.txt'] == base.files()['hash.txt'], f'a{align}: timed-out presses changed the run')
        just = Run(exe, disk2, bios, f'just_a{align}', out, inputs=(60, late - 4), align=align)
        check([p[1] for p in just.presses()] == ['peek', 'swap'], f'a{align}: press inside the toast {just.presses()}')

    # the toast: in the presentation while it is up, never in the picture
    act = out / 'action_a0'
    shown = hidden = 0
    for f in range(0, FRAMES, 10):
        same = digest(act / f'present_{f:05d}.png') == digest(act / f'shot_{f:05d}.png')
        up = 60 <= f and (f + 1) * 1000 / 60.0988 < (70 + HOLD) * 1000 / 60.0988 + 3000
        if f < 60:
            check(same, f'frame {f}: presentation differs before any press')
        elif up:
            check(not same, f'frame {f}: the toast is not in the presentation')
            shown += 1
        elif (f + 1) * 1000 / 60.0988 >= (70 + HOLD) * 1000 / 60.0988 + 3000 + 20:
            check(same, f'frame {f}: the toast stayed in the presentation')
            hidden += 1
    check(shown >= 10 and hidden >= 3, f'toast frames shown {shown}, hidden {hidden}')

    # cycling a 4-sided (two-disk) image: 1, 2, 3, then around to 0; a press during the hold retargets
    presses = [30, 40]                                   # peek, swap -> 1
    presses += [40 + HOLD + 10, 40 + 2 * HOLD + 20]      # -> 2, -> 3 (each after the last insert)
    presses += [40 + 3 * HOLD + 30]                      # -> 0 (wraps)
    cyc = Run(exe, disk4, bios, 'cycle4', out, inputs=tuple(presses))
    inserted = [s for _, w, s in cyc.changes() if w == 'I']
    check(inserted == [1, 2, 3, 0], f'4 sides: inserted {inserted}')
    check([p[1] for p in cyc.presses()] == ['peek', 'swap', 'swap', 'swap', 'swap'], f'4 sides: {cyc.presses()}')
    # after the boot load: peek, ->1, then ->2 and ->3 while the drive is held empty
    retarget = Run(exe, disk4, bios, 'retarget4', out, inputs=(150, 160, 170, 180))
    check([p[1] for p in retarget.presses()] == ['peek', 'swap', 'next side', 'next side'], f'{retarget.presses()}')
    check(retarget.changes() == [(160, 'E', 0), (180 + HOLD, 'I', 3)], f'retarget: {retarget.changes()}')
    check(retarget.ram[0x412] == 0x13 and retarget.ram[0x420] == 0xC3,
          f'retarget: the multi-disk program did not load disk 2 side B: t={retarget.ram[0x412]:02X}')
    print(f'disk action: {checks} checks passed')


if __name__ == '__main__':
    main()
