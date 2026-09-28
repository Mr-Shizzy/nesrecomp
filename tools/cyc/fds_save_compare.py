#!/usr/bin/env python3
"""FDS disk saves against nesref (Mesen): a local check on owner images, not CI.

Drives the cycle runtime and nesref through the same scripted input to a point
where the game writes its disk, then compares what each saved, then boots
each machine again on its saved disk and compares what the BIOS and the game
read back.

  1. save    nesref runs the --save-input route with an empty NESREF_SAVE_DIR;
             Mesen writes <stem>.ips at unload (an IPS patch of the image file,
             Core/FDS.cpp SaveBattery). cyc runs the same route with
             --save-file (its whole-stream disk save) and --fds-export-ips (the
             same disk as Mesen would save it). Reported: the two .ips byte for
             byte, the patched images, and which files the save changed.
  2. reload  nesref boots with its .ips in the save dir (Mesen re-applies it at
             load); cyc boots twice: from its own disk save, and from Mesen's
             .ips (--fds-import-ips, which rebuilds the disk in Mesen's layout).
             Each is compared frame by frame with nesref as the FDS gates do
             (tools/cyc/fds_oracle_gates.py: CPU RAM, PRG RAM, nametables,
             CHR RAM, picture).
  3. The image file's SHA-1 is the same before and after every run.

nesref frame numbering: an input line `F BUTTONS` of the cyc --input file
applies before frame F in cyc and at f=F in nesref (converted to nesref WAIT
steps, which advance n - 1 frames).

  python tools/cyc/fds_save_compare.py --cyc build/fds-murasame/nes_game.exe \\
      --nesref F:/Projects/nesref_wt-fds/nesref.exe --image murasame.fds --bios bios/disksys.rom \\
      --save-input mura_save.txt --save-frames 4000 --boot-input mura_boot.txt \\
      --boot-frames 1300,1400,1470,1600 --out out/
"""
import argparse
import concurrent.futures
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fds_oracle_gates as gates  # noqa: E402

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def sha1(path):
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()


nesref_script = gates.nesref_script


def run(cmd, cwd, log, env=None, timeout=3600):
    p = subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
                       creationflags=NO_WINDOW)
    Path(log).write_text(' '.join(str(c) for c in cmd) + '\n' + p.stdout + p.stderr)
    if p.returncode:
        raise RuntimeError(f'exit {p.returncode}; see {log}')
    return p.stdout


def nesref(args, work, extra, ips=None):
    work.mkdir(parents=True, exist_ok=True)
    save = work / 'save'
    shutil.rmtree(save, ignore_errors=True)
    save.mkdir()
    if ips:
        shutil.copyfile(ips, save / (args.image.stem + '.ips'))
    env = gates.nesref_env(args, args.sysdir, save, {'NESREF_FDS_BOOT_DISK': args.boot_disk, **extra})
    run([args.nesref, args.core, args.image], work, work / 'nesref.log', env)
    return save


def nesref_frame(args, root, frame, script, ips):
    work = root / f'f{frame:05d}'
    shot, state = work / 'shot.png', work / 'state.bin'
    if not (shot.exists() and state.exists()):
        work.mkdir(parents=True, exist_ok=True)
        (work / 'script.txt').write_text('\n'.join(script) + '\n')
        nesref(args, work, {'NESREF_FRAMES': str(frame), 'NESREF_SHOT': str(frame), 'NESREF_SHOT_FILE': str(shot),
                            'NESREF_STATEDUMP': f'{frame}:{state}', 'NESREF_TRACE_FILE': str(work / 'trace.jsonl'),
                            'NESREF_SCRIPT': str(work / 'script.txt')}, ips)
    return frame, gates.read_png_rgb(shot), gates.parse_state(state.read_bytes())


def cyc(args, work, name, frames, extra):
    work.mkdir(parents=True, exist_ok=True)
    cmd = [args.cyc, args.image, '--fds-bios', args.bios, '--frames', frames, '--fds-boot-disk', args.boot_disk,
           '--ring-out', work / f'{name}.ring'] + args.cyc_args.split() + extra
    return run(cmd, work, work / f'{name}.log')


# ---------------------------------------------------------------- disk content
def files_of(image):
    """{(side, index): (name, load, data)} of an image, walking every block."""
    head = 16 if image[:4] == b'FDS\x1a' else 0
    sides = image[4] if head else len(image) // 65500
    out = {}
    for s in range(sides):
        d = image[head + s * 65500: head + (s + 1) * 65500]
        p, i, size = 58, 0, 0
        while p < len(d) and d[p] == 3:
            size = d[p + 13] | d[p + 14] << 8
            name = d[p + 3:p + 11].decode('latin-1')
            load = d[p + 11] | d[p + 12] << 8
            p += 16
            if p >= len(d) or d[p] != 4:
                break
            out[(s, i)] = (name, load, d[p + 1:p + 1 + size])
            p += 1 + size
            i += 1
    return out


def ips_apply(ips, data):
    assert ips[:5] == b'PATCH'
    out, p = bytearray(data), 5
    while ips[p:p + 3] != b'EOF':
        addr = int.from_bytes(ips[p:p + 3], 'big')
        n = int.from_bytes(ips[p + 3:p + 5], 'big')
        p += 5
        if n == 0:
            count, value = int.from_bytes(ips[p:p + 2], 'big'), ips[p + 2]
            p += 3
            chunk = bytes([value]) * count
        else:
            chunk = ips[p:p + n]
            p += n
        if addr + len(chunk) > len(out):
            out.extend(bytes(addr + len(chunk) - len(out)))
        out[addr:addr + len(chunk)] = chunk
    return bytes(out)


def montage(path, pairs):
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return False
    w, h = 256, 240
    m = Image.new('RGB', (len(pairs) * (w + 4), 2 * (h + 14)), 'black')
    d = ImageDraw.Draw(m)
    for i, (label, top, bottom) in enumerate(pairs):
        x = i * (w + 4)
        for r, (tag, png) in enumerate((('cyc', top), ('nesref', bottom))):
            if png and Path(png).exists():
                m.paste(Image.open(png).convert('RGB').resize((w, h)), (x, r * (h + 14) + 14))
            d.text((x + 2, r * (h + 14) + 1), f'{tag} {label}', fill='yellow')
    m.save(path)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--cyc', type=Path, required=True)
    ap.add_argument('--cyc-args', default='', help='extra cyc arguments for every run')
    ap.add_argument('--nesref', type=Path, required=True)
    ap.add_argument('--core', type=Path)
    ap.add_argument('--image', type=Path, required=True)
    ap.add_argument('--bios', type=Path, required=True)
    ap.add_argument('--save-input', type=Path, required=True)
    ap.add_argument('--save-frames', type=int, required=True)
    ap.add_argument('--boot-input', type=Path, required=True)
    ap.add_argument('--boot-frames', required=True, help='nesref frames to compare on the reload, a,b,c')
    ap.add_argument('--boot-disk', default='0')
    ap.add_argument('--write-at', default='head', choices=['head', 'mesen'])
    ap.add_argument('--jobs', type=int, default=8)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    args.core = args.core or args.nesref.parent / 'cores' / 'mesen_libretro.dll'
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    original = args.image.resolve()
    before = sha1(original)
    args.sysdir = out / 'system'
    args.sysdir.mkdir(exist_ok=True)
    shutil.copyfile(args.bios, args.sysdir / 'disksys.rom')
    args.image = out / ('disk' + original.suffix.lower())
    shutil.copyfile(original, args.image)
    image = args.image.read_bytes()
    failures = []

    # 1. save
    script = nesref_script(args.save_input, args.save_frames)
    (out / 'save_script.txt').write_text('\n'.join(script) + '\n')
    save_dir = nesref(args, out / 'nesref_save', {'NESREF_FRAMES': str(args.save_frames),
                                                  'NESREF_SCRIPT': str(out / 'save_script.txt'),
                                                  'NESREF_TRACE_FILE': str(out / 'nesref_save' / 'trace.jsonl')})
    mesen_ips = save_dir / (args.image.stem + '.ips')
    if not mesen_ips.exists():
        raise SystemExit(f'nesref wrote no disk save on this route ({save_dir})')
    shutil.copyfile(mesen_ips, out / 'mesen.ips')
    disk_save, cyc_ips = out / 'cyc.fdssave', out / 'cyc.ips'
    disk_save.unlink(missing_ok=True)
    stdout = cyc(args, out / 'cyc_save', 'save', args.save_frames,
                 ['--input', args.save_input, '--save-file', disk_save, '--fds-export-ips', cyc_ips,
                  '--fds-write-at', args.write_at])
    print(next((l for l in stdout.splitlines() if l.startswith('fds: side')), '').strip())
    a, b = (out / 'mesen.ips').read_bytes(), cyc_ips.read_bytes()
    pa, pb = ips_apply(a, image), ips_apply(b, image)
    print(f'save: Mesen .ips {len(a)} bytes, cyc export {len(b)} bytes: '
          f'{"byte-identical" if a == b else "DIFFERENT"}; patched images {"identical" if pa == pb else "DIFFERENT"}')
    if pa != pb:
        failures.append('saved disk content differs from Mesen')
    orig_files, saved_files = files_of(image), files_of(pa)
    for key in sorted(set(orig_files) | set(saved_files)):
        o, s = orig_files.get(key), saved_files.get(key)
        if o != s:
            diff = sum(x != y for x, y in zip(o[2], s[2])) if o and s else None
            print(f'  side {key[0]} file {key[1]} {(s or o)[0]!r} at ${(s or o)[1]:04X}: '
                  f'{diff} of {len((s or o)[2])} data bytes changed by the save')

    # 2. reload
    frames = [int(x) for x in args.boot_frames.split(',')]
    boot = nesref_script(args.boot_input, max(frames) + 2)
    (out / 'boot_script.txt').write_text('\n'.join(boot) + '\n')
    trace_dir = out / 'nesref_boot'
    nesref(args, trace_dir, {'NESREF_FRAMES': str(max(frames) + 2), 'NESREF_SCRIPT': str(out / 'boot_script.txt'),
                             'NESREF_TRACE_FILE': str(trace_dir / 'trace.jsonl')}, out / 'mesen.ips')
    ref_ram = gates.ram_trace(trace_dir / 'trace.jsonl')
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        ref = sorted(pool.map(lambda f: nesref_frame(args, out / 'nesref_frames', f, boot, out / 'mesen.ips'), frames))
    pairs = []
    for name, extra in (('own-save', ['--save-file', out / 'boot.fdssave']),
                        ('mesen-ips', ['--fds-import-ips', out / 'mesen.ips'])):
        shutil.copyfile(disk_save, out / 'boot.fdssave')
        work = out / f'cyc_boot_{name}'
        log = work / 'frames.bin'
        shots = work / 'shot.png'
        cyc(args, work, 'boot', max(frames) + 4,
            ['--input', args.boot_input, '--ram-init', 'zeros', '--frame-log', log, '--frame-log-at', 'mesen',
             '--screenshot', shots, '--shot-every', '1', '--fds-write-at',
             args.write_at] + extra)
        rec = gates.read_frame_log(log)
        offset, score = gates.pairing_offset(rec, ref_ram, frames)
        with open(work / 'compare.jsonl', 'w') as report:
            rows, first = gates.compare(rec, ref_ram, ref, offset, report)
        print(f'reload from {name}: pairing nesref f = cyc record {offset:+d} (CPU RAM equal on {score}/{len(frames)})')
        gates.summarize(f'  {name}', rows, first)
        if name == 'own-save':
            same = (out / 'boot.fdssave').read_bytes() == disk_save.read_bytes()
            print(f'  the disk save {"was not rewritten" if same else "CHANGED"} by the reload run')
        for f in frames:
            cyc_png = shots.with_name(f'shot_{f - 1:05d}.png')
            if name == 'own-save':
                pairs.append((f'f={f}', cyc_png, out / 'nesref_frames' / f'f{f:05d}' / 'shot.png'))
    for f in list(out.glob('cyc_boot_*/shot_*.png')):
        n = int(f.stem.split('_')[1])
        if n + 1 not in frames:
            f.unlink()
    if montage(out / 'reload_own_save_vs_nesref.png', pairs):
        print(f'montage: {out / "reload_own_save_vs_nesref.png"}')
    after = sha1(original)
    print(f'image {original.name}: SHA-1 {before} before, {after} after: {"unchanged" if before == after else "CHANGED"}')
    if before != after:
        failures.append('the image changed')
    print('PASS' if not failures else 'FAIL: ' + '; '.join(failures))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
