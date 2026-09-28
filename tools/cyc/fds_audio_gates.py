#!/usr/bin/env python3
"""FDS sound unit against nesref (Mesen): a local check on owner files, not CI.

One route (a cyc --input file: button lines and DISK_EJECT / DISK_SELECT /
DISK_INSERT lines, converted to a NESREF_SCRIPT with nesref's WAIT n = n - 1
frames rule) is run on a cyc build and on nesref, and compared three ways:

  model   the cyc run replayed through tools/cyc/fds_audio_model.py (every
          $4040-$4092 read and every frame's sound state; exact)
  state   the sound unit's state at each nesref frame f against cyc's frame
          log record f - offset (--frame-log-at mesen): the FdsAudio
          snapshot in Mesen's savestate (one NESREF_STATEDUMP run per frame,
          --jobs in parallel) against cyc_fds_audio_state(), byte for byte.
          Table entries the program never wrote are skipped (Mesen leaves
          them as uninitialized heap). A frame whose only differences go away
          when one side is stepped 1-8 cycles with no access is reported as
          a phase difference (where the two emulators end a frame), not a
          synthesis difference; one whose only difference is a stopped wave's
          accumulator off by k cycles of one of the last pitches it ran at is a
          write timing difference (the write that started or stopped the note came
          k cycles apart: the two emulators take NMIs a few cycles apart on
          some frames, visible as a different return address on the stack).
          CPU RAM (nesref's trace) is compared too.
  pcm     cyc --wav-out against NESREF_WAV with the certified A/B analyzer
          (tools/nes_audio_ab.py compare(): timbre L1, log-f pitch cents,
          onsets, drift; nes-audio-fidelity), plus DC and RMS per stream and
          the cyc self-floor (native against --interp-only, which must be
          bit-identical). The comparison is repeated with the runtime's
          console output stage (90/440 Hz high-pass, 14 kHz low-pass, which
          Mesen leaves out) applied to nesref's PCM, which separates synthesis
          from output-stage differences. --pcm-window A:B limits it to frames
          A..B.

  python tools/cyc/fds_audio_gates.py --cyc build/fds-otocky/nes_game.exe --image Otocky.fds \\
      --bios disksys.rom --input route.txt --frames 3500:6400 --nesref F:/Projects/nesref_wt-fds/nesref.exe \\
      --out out/otocky [--state-frames active|A:B|a,b] [--pcm-window A:B]

Writes into --out: cyc.wav, nesref.wav, cyc.frames, cyc.ring, script.txt,
state.jsonl, abaudio.json, and a summary (stdout).
"""
import argparse
import concurrent.futures
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import fds_audio_model as model              # noqa: E402
from fds_oracle_gates import ram_trace, read_frame_log, run_nesref  # noqa: E402

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
BUTTONS = ('A', 'B', 'SELECT', 'START', 'UP', 'DOWN', 'LEFT', 'RIGHT')
TABLE_FIELDS = ('wave', 'mod_table')


# ---------------------------------------------------------------- the route
def parse_route(path):
    """{frame: [('buttons', set) | ('disk', command words)]} from a cyc --input file."""
    steps = {}
    for line in Path(path).read_text().splitlines():
        words = line.split('#')[0].split()
        if not words:
            continue
        frame = int(words[0])
        rest = words[1:]
        if rest and rest[0].startswith('DISK_'):
            steps.setdefault(frame, []).append(('disk', rest))
        else:
            held = set() if not rest or rest[0] == '-' else set(rest[0].split('+'))
            assert held <= set(BUTTONS), line
            steps.setdefault(frame, []).append(('buttons', held))
    return steps


def nesref_script(steps, last_frame):
    """nesref applies the commands after `WAIT n` n - 1 frames later (its WAIT
    counts the frame it starts on); cyc applies a line at frame F before
    frame F runs, which is nesref's f = F."""
    out, now = [], 0
    for frame in sorted(steps):
        out.append(f'WAIT {frame - now + 1}')
        now = frame
        for kind, what in steps[frame]:
            if kind == 'disk':
                out.append(' '.join(what))        # same commands and side names in both
            else:
                out += [f'RELEASE {b}' for b in BUTTONS] + [f'HOLD {b}' for b in sorted(what)]
    out.append(f'WAIT {last_frame - now + 10}')
    return '\n'.join(out) + '\n'


# ---------------------------------------------------------- Mesen's state
def _u32(b, o):
    return struct.unpack_from('<I', b, o)[0]


def _block(b, o):
    n = _u32(b, o)
    assert _u32(b, o + 4) == n
    return o + 8, n


def _nested(b, o):
    s = _u32(b, o)
    assert _u32(b, o + 4) == s and _u32(b, o + 8) == s - 4
    return o + 12, s - 4


def mesen_audio_state(blob):
    """The FdsAudio snapshot of a Mesen 0.9.9-DLL savestate (FDS field block
    of 314 bytes, FdsAudio nested at +37: see nesref frontend.cpp mesen_parse)
    in cyc_fds_audio_state()'s layout. 0.9.9 wraps every Stream() call in a
    block; a nested Snapshotable is [s][s][s-4][stream]."""
    f = None
    for h in range(len(blob) - 400):
        if _u32(blob, h) == 314 and _u32(blob, h + 4) == 314:
            f = blob[h + 8:h + 8 + 314]
            break
    assert f is not None, 'no FDS field block in the state'
    p, n = _nested(f, 37)
    end = p + n
    p, n = _block(f, p)
    assert p + n == end
    q, m = _nested(f, p)                      # BaseFdsChannel _volume
    r, k = _block(f, q)
    assert k == 11
    vol = f[r:r + 11]
    p = q + m
    q, m = _nested(f, p)                      # ModChannel _mod: base block, then its own
    r, k = _block(f, q)
    assert k == 11
    mod = f[r:r + 11]
    r2, k2 = _block(f, r + 11)
    assert k2 == 77 and _u32(f, r2 + 5) == 64
    md = f[r2:r2 + 77]
    p = q + m
    rest = f[p:end]                           # waveWrite ... lastOutput, then the wavetable array
    assert len(rest) == 80 and _u32(rest, 12) == 64
    state = vol + mod + md[0:5] + md[9:73] + md[73:77] + rest[0:12] + rest[16:80]
    assert len(state) == model.STATE_BYTES
    return state


def masked(blob, mask):
    """Leave out what the two emulators cannot agree on by construction: table
    entries never written (Mesen: uninitialized heap) and an envelope timer no
    write has loaded yet, which runs down from 0 since power-on and so counts
    the cycles since power-on (reported separately)."""
    s = model.decode(blob)
    s['wave'] = bytes(v if ok else 0 for v, ok in zip(s['wave'], mask['wave']))
    s['mod_table'] = bytes(v if ok else 0 for v, ok in zip(s['mod_table'], mask['mod']))
    for ch in ('vol', 'mod'):
        if not mask[ch + '_loaded']:
            s[ch + '_timer'] = 0
    return model.encode(s)


FULL_MASK = {'wave': [True] * 64, 'mod': [True] * 64, 'vol_loaded': True, 'mod_loaded': True, 'pitches': []}


def timing_explained(a, b, pitches, limit=8):
    """(k, pitch): the wave is stopped in both and its accumulator differs by
    k cycles of one of the last pitches it ran at, i.e. a write that changed
    the pitch came k cycles apart in the two emulators. The rest must be equal."""
    da, db = model.decode(a), model.decode(b)
    if set(model.diff_fields(a, b)) - {'wave_overflow', 'wave_pos'}:
        return None
    delta = (db['wave_overflow'] - da['wave_overflow']) & 0xFFFF
    for k in range(1, limit + 1):
        for sign in (1, -1):
            for pitch in pitches:
                if (sign * k * pitch) & 0xFFFF == delta:
                    return sign * k
    return None


# ---------------------------------------------------------------- the runs
def run_cyc(args, out, frames, extra, name):
    cmd = [str(args.cyc), str(args.image), '--fds-bios', str(args.bios), '--frames', str(frames),
           '--ram-init', 'zeros', '--fds-boot-disk', args.boot] + extra + args.cyc_args.split()
    if args.input:
        cmd += ['--input', str(args.input)]
    p = subprocess.run(cmd, cwd=out, capture_output=True, text=True, timeout=3600, creationflags=NO_WINDOW)
    (out / f'{name}.log').write_text(' '.join(cmd) + '\n' + p.stdout + p.stderr)
    if p.returncode:
        raise RuntimeError(f'cyc exit {p.returncode}; see {out / name}.log')
    return p.stdout


def nesref_state(args, root, frame, script):
    work = root / f'f{frame:05d}'
    state = work / 'state.bin'
    if not state.exists():
        work.mkdir(parents=True, exist_ok=True)
        extra = {'NESREF_FRAMES': str(frame), 'NESREF_STATEDUMP': f'{frame}:{state}',
                 'NESREF_TRACE_FILE': str(work / 'trace.jsonl'), 'NESREF_FDS_BOOT_DISK': args.boot}
        if script:
            (work / 'script.txt').write_text(script)
            extra['NESREF_SCRIPT'] = str(work / 'script.txt')
        run_nesref(args, work, extra, work / 'nesref.log')
        (work / 'trace.jsonl').unlink(missing_ok=True)
    return frame, mesen_audio_state(state.read_bytes())


def frame_spec(spec, active):
    if spec == 'active':
        return sorted(active)
    if ':' in spec:
        a, b = spec.split(':')
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in spec.split(',')]


def active_frames(ring_path):
    """Frames in which the sound unit stepped (ring fds.audio), and the next."""
    frames = set()
    for line in open(ring_path):
        p = line.split()
        if len(p) > 3 and p[3] == 'fds.audio':
            frames |= {int(p[1]), int(p[1]) + 1}
    return frames


def phase_explained(cyc_blob, ref_blob, mask, limit=8):
    """k > 0: stepping cyc k cycles gives Mesen's state; k < 0: the reverse."""
    for k in range(1, limit + 1):
        if masked(model.step(cyc_blob, k), mask) == masked(ref_blob, mask):
            return k
        if masked(model.step(ref_blob, k), mask) == masked(cyc_blob, mask):
            return -k
    return None


def console_output_stage(x, rate):
    """The runtime's output stage (hw_apu.c audio_emit: 90 Hz and 440 Hz
    high-pass, 14 kHz low-pass, nesdev APU Mixer), which Mesen does not
    apply. Run on nesref's PCM it separates synthesis differences from the
    output stage in the A/B numbers."""
    import math
    import numpy as np
    dt = 1.0 / rate
    rc = [1.0 / (2 * math.pi * f) for f in (90.0, 440.0, 14000.0)]
    a90, a440, alp = rc[0] / (rc[0] + dt), rc[1] / (rc[1] + dt), dt / (rc[2] + dt)
    y = np.empty_like(x)
    i1 = o1 = i2 = o2 = lp = 0.0
    for n, v in enumerate(x.tolist()):
        o1 = a90 * (o1 + v - i1)
        i1 = v
        o2 = a440 * (o2 + o1 - i2)
        i2 = o1
        lp += alp * (o2 - lp)
        y[n] = lp
    return y


def write_wav(path, x, rate):
    import wave
    import numpy as np
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype('<i2').tobytes())


def pcm_report(args, out, cyc_wav, ref_wav, floor_wav):
    import nes_audio_ab as ab
    import numpy as np
    if args.pcm_window:
        a, b = (int(x) for x in args.pcm_window.split(':'))
        start_s, dur_s = a / 60.0988, (b - a) / 60.0988
    else:
        start_s = dur_s = 0.0
    try:
        result = {'ab': ab.compare(str(ref_wav), str(cyc_wav), start_s=start_s, dur_s=dur_s)}
    except RuntimeError as e:                 # nes_audio_ab: under 1 s of sound in both
        print(f'  pcm: not compared ({e})')
        return None
    staged = out / 'nesref_console_stage.wav'
    x, rate = ab.load_wav(str(ref_wav))
    write_wav(staged, console_output_stage(x, rate), rate)
    result['ab_console_stage'] = ab.compare(str(staged), str(cyc_wav), start_s=start_s, dur_s=dur_s)
    if floor_wav:
        result['floor'] = ab.compare(str(cyc_wav), str(floor_wav), start_s=start_s, dur_s=dur_s)
        result['floor_bit_identical'] = Path(cyc_wav).read_bytes() == Path(floor_wav).read_bytes()
    for tag, path in (('cyc', cyc_wav), ('nesref', ref_wav)):
        x, rate = ab.load_wav(str(path))
        i0 = int(start_s * rate)
        seg = x[i0:i0 + int(dur_s * rate)] if dur_s else x[i0:]
        result[tag] = {'rate': rate, 'seconds': len(seg) / rate, 'dc': float(np.mean(seg)),
                       'rms_ac': float(np.std(seg)), 'rms_ac_dc_blocked': float(np.std(ab.dc_block(seg, rate)))}
    (out / 'abaudio.json').write_text(json.dumps(result, indent=2, default=float))
    ab.print_report(result['ab'], 'cyc vs nesref (Mesen)')
    t = result['ab_console_stage']
    print(f"  with the runtime's output stage applied to nesref: timbre L1 {t['timbre']['band_l1']:.3f}, "
          f"pitch {t['pitch']['logf_psd']['cents']:+.2f} cents, onsets "
          f"{(t['onset'] or {}).get('match_rate', 0) * 100:.0f}%")
    if 'floor' in result:
        print(f"  self-floor (native vs --interp-only): bit-identical={result['floor_bit_identical']}, "
              f"timbre L1 {result['floor']['timbre'].get('band_l1', 0):.3f}")
    for tag in ('cyc', 'nesref'):
        r = result[tag]
        print(f"  {tag}: {r['seconds']:.1f} s at {r['rate']} Hz, DC {r['dc']:+.5f}, RMS {r['rms_ac']:.5f} "
              f"(DC-blocked {r['rms_ac_dc_blocked']:.5f})")
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--cyc', type=Path, required=True, help='cyc_interp or a compiled FDS program')
    ap.add_argument('--cyc-args', default='')
    ap.add_argument('--nesref', type=Path, required=True)
    ap.add_argument('--core', type=Path)
    ap.add_argument('--image', type=Path, required=True)
    ap.add_argument('--bios', type=Path, required=True)
    ap.add_argument('--input', type=Path, help='cyc --input route (buttons and disk commands)')
    ap.add_argument('--boot', default='0', help="side in the drive at power-on (both machines): none or a side")
    ap.add_argument('--frames', type=int, required=True, help='frames to run')
    ap.add_argument('--state-frames', default='active', help="nesref frames to compare states: active, A:B or a,b,c")
    ap.add_argument('--pcm-window', help='frames A:B of the PCM comparison (default: the whole run)')
    ap.add_argument('--no-floor', action='store_true', help='skip the --interp-only self-floor run')
    ap.add_argument('--offset', type=int, help='nesref frame - cyc frame record; measured from CPU RAM if omitted')
    ap.add_argument('--jobs', type=int, default=8)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    args.core = args.core or args.nesref.parent / 'cores' / 'mesen_libretro.dll'
    return run(args)


def run(args):
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    args.sysdir = out / 'system'
    args.sysdir.mkdir(exist_ok=True)
    shutil.copyfile(args.bios, args.sysdir / 'disksys.rom')
    image = out / ('disk' + args.image.suffix.lower())
    shutil.copyfile(args.image, image)
    args.image, args.bios = image, (args.sysdir / 'disksys.rom')
    if args.input:
        shutil.copyfile(args.input, out / 'route.txt')
        args.input = out / 'route.txt'
    steps = parse_route(args.input) if args.input else {}
    script = nesref_script(steps, args.frames)
    (out / 'script.txt').write_text(script)

    # cyc: frame log at Mesen's frame end, ring, PCM; and the self-floor
    print(run_cyc(args, out, args.frames, ['--frame-log', 'cyc.frames', '--frame-log-at', 'mesen',
                                           '--ring-out', 'cyc.ring', '--wav-out', 'cyc.wav'], 'cyc').strip())
    floor = None
    if not args.no_floor:
        run_cyc(args, out, args.frames, ['--interp-only', '--wav-out', 'cyc_interp.wav'], 'cyc_interp')
        floor = out / 'cyc_interp.wav'

    # 1. the model replay, recording which table entries are defined at each snapshot
    masks = {}
    reads, snaps, bad, start = model.replay_run(
        out / 'cyc.ring', out / 'cyc.frames',
        on_snapshot=lambda c, m: masks.__setitem__(c, {'wave': list(m.wave_defined), 'mod': list(m.mod_defined),
                                                       'vol_loaded': m.vol.loaded, 'mod_loaded': m.mod.loaded,
                                                       'pitches': list(m.recent_pitches)}))
    print(f'model: {reads} sound reads and {snaps} frame states replayed'
          f"{'' if start is None else f' (from the snapshot at cycle {start}: the ring had evicted older events)'}"
          f': {len(bad)} differences')
    for line in bad[:5]:
        print('  ' + line)

    # 2. nesref: one long run for CPU RAM and PCM
    ref_wav, trace = out / 'nesref.wav', out / 'nesref_trace.jsonl'
    if not (ref_wav.exists() and trace.exists()):
        extra = {'NESREF_FRAMES': str(args.frames), 'NESREF_TRACE_FILE': str(trace), 'NESREF_WAV': str(ref_wav),
                 'NESREF_FDS_BOOT_DISK': args.boot, 'NESREF_SCRIPT': str(out / 'script.txt')}
        run_nesref(args, out, extra, out / 'nesref.log')
    ref_ram = ram_trace(trace)
    cyc = read_frame_log(out / 'cyc.frames')
    if args.offset is None:
        best = max(range(-3, 4), key=lambda off: sum(
            1 for f in ref_ram if (f - off) in cyc and cyc[f - off]['cpu'] == ref_ram[f]))
        args.offset = best
    same_ram = sum(1 for f in ref_ram if (f - args.offset) in cyc and cyc[f - args.offset]['cpu'] == ref_ram[f])
    print(f'pairing: nesref frame f = cyc record f - {args.offset}; CPU RAM identical on {same_ram}/{len(ref_ram)} frames')

    # 3. states
    frames = [f for f in frame_spec(args.state_frames, {f + args.offset for f in active_frames(out / 'cyc.ring')})
              if 0 < f <= args.frames and (f - args.offset) in cyc]
    cycles_of = {k: rec['cycles'] for k, rec in cyc.items()}
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        ref_states = dict(pool.map(lambda f: nesref_state(args, out / 'nesref', f, script), frames))
    exact, phase, differ, rows, poweron, timing = 0, {}, [], [], {}, {}
    for f in frames:
        rec = cyc[f - args.offset]
        mine, theirs = rec['fds_audio'], ref_states[f]
        mask = masks.get(cycles_of[f - args.offset], FULL_MASK)
        a, b = masked(mine, mask), masked(theirs, mask)
        for ch in ('vol', 'mod'):
            if not mask[ch + '_loaded']:
                d = model.decode(theirs)[ch + '_timer'] - model.decode(mine)[ch + '_timer']
                poweron[d] = poweron.get(d, 0) + 1
        row = {'frame': f, 'cpu_ram_equal': f in ref_ram and rec['cpu'] == ref_ram[f]}
        if a == b:
            exact += 1
            row['state'] = 'exact'
        else:
            k = phase_explained(mine, theirs, mask)
            t = None if k is not None else timing_explained(a, b, mask['pitches'])
            if k is not None:
                phase[k] = phase.get(k, 0) + 1
                row['state'] = f'phase {k:+d}'
            elif t is not None:
                timing[t] = timing.get(t, 0) + 1
                row['state'] = f'write timing {t:+d}'
            else:
                fields = model.diff_fields(a, b)
                da, db = model.decode(a), model.decode(b)
                row['state'] = 'differs'
                row['fields'] = {n: [da[n] if n not in TABLE_FIELDS else da[n].hex(),
                                     db[n] if n not in TABLE_FIELDS else db[n].hex()] for n in fields}
                differ.append(row)
        rows.append(row)
    with open(out / 'state.jsonl', 'w') as fh:
        for row in rows:
            fh.write(json.dumps(row) + '\n')
    print(f'states: {len(frames)} frames; exact {exact}, phase-only {sum(phase.values())} '
          f'({", ".join(f"{k:+d} cycles: {n}" for k, n in sorted(phase.items()))}), stopped-wave accumulator '
          f'off by whole cycles of its pitch {sum(timing.values())} '
          f'({", ".join(f"{k:+d}: {n}" for k, n in sorted(timing.items()))}), differ {len(differ)}')
    if poweron:
        print('  envelope timers still running from power-on (Mesen - cyc, i.e. clocks cyc ran more): ' +
              ', '.join(f'{d:+d}: {n} frames' for d, n in sorted(poweron.items())))
    for row in differ[:5]:
        print(f"  frame {row['frame']}: " + ', '.join(f'{n} cyc={v[0]} mesen={v[1]}' for n, v in row['fields'].items()))

    # 4. PCM
    pcm_report(args, out, out / 'cyc.wav', ref_wav, floor)
    ok = not bad and not differ
    print('PASS' if ok else 'DIFFERENCES (see state.jsonl)')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
