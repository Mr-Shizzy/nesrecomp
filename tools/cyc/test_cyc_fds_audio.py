#!/usr/bin/env python3
"""The FDS sound unit against an independent model (CTest cyc_fds_audio_test; no owner data).

tools/cyc/fds_audio_fixtures.py writes two synthetic FDS programs (sound: every
behaviour the CPU can observe; pcm: a pulse tone, then an FDS square). Each is
compiled from its disk (NESRecomp <disk> --fds-bios, identity from the
synthetic BIOS's .toml) and run native, --interp-only and on the standalone
cyc_interp at all four CPU/PPU alignments:

  sound  the --hash-out traces (every bus access, memories, picture, hardware
         state including the sound unit) must be identical; each alignment's
         run is replayed through tools/cyc/fds_audio_model.py (a transliteration
         of Mesen's FdsAudio written independently of hw_fds_audio.c): every
         $4040-$4092 read in the event ring and the sound unit's state at every
         frame end must match exactly, and the ring's fds.env / fds.audio
         events must count what the model counts (gain changes per envelope,
         wave and mod steps per frame). The mesen2 and hardware profiles are
         replayed against the model's mesen2 variant the same way.
  pcm    native and --interp-only PCM are identical; the pulse and the FDS
         square measure at their pitch (within 1 Hz); the ratio of their
         fundamentals is Mesen's mix (FDS 20 units per step against 477600 / (8128/n + 100),
         1.69x) in the mesen profile, and the nesdev level (2.4x) through a
         ~2 kHz low-pass that dulls the FDS square (not the pulse) in the
         hardware profile.

python tools/cyc/test_cyc_fds_audio.py --recompiler build/compiler/NESRecomp.exe \\
    --interp build/cyc/cyc_interp.exe --out build/cyc-fds-audio [--cmake ... --generator ... --make-program ...]
"""
import argparse
import math
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import wave

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fds_audio_fixtures as fixtures      # noqa: E402
import fds_audio_model as model            # noqa: E402

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
SOUND_FRAMES, PCM_FRAMES = 240, 1100


def run(cmd, cwd, log, timeout=900):
    p = subprocess.run([str(x) for x in cmd], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                       creationflags=NO_WINDOW)
    Path(log).write_text(' '.join(str(x) for x in cmd) + '\n' + p.stdout + p.stderr)
    if p.returncode:
        raise RuntimeError(f'exit {p.returncode}: {cmd}; see {log}')
    return p


def build(args, out, name, source):
    folder = out / name
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    (folder / 'game.toml').write_text(f'[game]\noutput_prefix = "{name}"\ncycle_accurate = true\nfds = true\n\n'
                                      f'[fds]\nimage = "../{name}.fds"\nbios = "../bios/{name}.rom"\n', newline='\n')
    run([args.recompiler.resolve(), '--game', 'game.toml'], folder, folder / 'codegen.log')
    (folder / 'CMakeLists.txt').write_text('\n'.join([
        'cmake_minimum_required(VERSION 3.20)', f'project(cyc_fds_audio_{name} C)', 'set(CMAKE_C_STANDARD 11)',
        f'include("{source.as_posix()}/cyc.cmake")',
        'file(GLOB GEN CONFIGURE_DEPENDS "generated/*_cyc*.c")',
        f'add_executable({name} ${{NESRECOMP_CYC_SOURCES}} ${{GEN}})',
        f'target_include_directories({name} PRIVATE ${{NESRECOMP_CYC_INCLUDE_DIRS}})',
        f'target_link_libraries({name} PRIVATE ${{NESRECOMP_CYC_LIBRARIES}})',
        f'target_compile_definitions({name} PRIVATE _CRT_SECURE_NO_WARNINGS)']) + '\n')
    command = [args.cmake, '-S', folder, '-B', folder / 'build', f'-DCMAKE_BUILD_TYPE={args.config}']
    if args.generator:
        command += ['-G', args.generator]
    if args.make_program:
        command += [f'-DCMAKE_MAKE_PROGRAM={args.make_program}']
    if args.c_compiler:
        command += [f'-DCMAKE_C_COMPILER={args.c_compiler}']
    run(command, folder, folder / 'configure.log')
    run([args.cmake, '--build', folder / 'build', '--config', args.config, '--parallel', '8'], folder, folder / 'build.log')
    suffix = '.exe' if NO_WINDOW else ''
    for exe in (folder / 'build' / args.config / (name + suffix), folder / 'build' / (name + suffix)):
        if exe.exists():
            return exe, folder
    raise AssertionError(f'{name}: no executable built')


def ring_audio_counts(ring_path):
    """Per frame: fds.audio (wave, mod steps) and fds.env gain changes per envelope."""
    audio, env = {}, {}
    for line in open(ring_path):
        p = line.split()
        if len(p) < 5 or line.startswith('#'):
            continue
        frame = int(p[1])
        if p[3] == 'fds.audio':
            audio[frame] = (int(p[4].split('=')[1]), int(p[5].split('=')[1]))
        elif p[3] == 'fds.env':
            which = 0 if p[4] == 'volume' else 1
            n = int(p[-1][1:]) if p[-1].startswith('x') else 1
            env[(frame, which)] = env.get((frame, which), 0) + n
    return audio, env


def check_replay(tag, ring, frames, profile):
    counts = []

    def snap(cycle, m):
        counts.append((cycle, m.wave_steps, m.mod_steps, m.vol.changes, m.mod.changes))

    reads, snaps, bad, start = model.replay_run(ring, frames, profile, snap)
    if start is not None:
        raise AssertionError(f'{tag}: the ring evicted events; the test needs every event since power-on')
    if bad:
        raise AssertionError(f'{tag}: {len(bad)} differences from the model, first: {bad[0]}')
    if reads < 20000 or snaps < SOUND_FRAMES:
        raise AssertionError(f'{tag}: only {reads} reads / {snaps} frames checked')
    # Ring summaries: frame k's events come between snapshots k-1 and k.
    audio, env = ring_audio_counts(ring)
    prev = (0, 0, 0, 0)
    for k, (_, wave_steps, mod_steps, vol_changes, mod_changes) in enumerate(counts):
        d = (wave_steps - prev[0], mod_steps - prev[1], vol_changes - prev[2], mod_changes - prev[3])
        prev = (wave_steps, mod_steps, vol_changes, mod_changes)
        want_audio = (d[0], min(d[1], 0xFFFF)) if d[0] or d[1] else None
        if audio.get(k) != want_audio:
            raise AssertionError(f'{tag}: frame {k} fds.audio {audio.get(k)}, model {want_audio}')
        for which in (0, 1):
            if env.get((k, which), 0) != d[2 + which]:
                raise AssertionError(f'{tag}: frame {k} fds.env[{which}] {env.get((k, which), 0)}, model {d[2 + which]}')
    total_env = sum(env.values())
    if total_env < 50 or not any(w for w, _ in audio.values()) or not any(m for _, m in audio.values()):
        raise AssertionError(f'{tag}: the program exercised too little ({total_env} envelope changes)')
    return reads, snaps


def load_pcm(path):
    with wave.open(str(path)) as f:
        rate, data = f.getframerate(), f.readframes(f.getnframes())
        assert f.getnchannels() == 1 and f.getsampwidth() == 2
    return rate, struct.unpack('<' + 'h' * (len(data) // 2), data)


def tone_windows(samples, rate):
    """(start, end) sample ranges of the non-silent stretches of at least 0.5 s."""
    block, out, start = rate // 20, [], None
    for i in range(0, len(samples) - block, block):
        seg = samples[i:i + block]
        loud = max(seg) - min(seg) > 200
        if loud and start is None:
            start = i
        elif not loud and start is not None:
            if i - start > rate // 2:
                out.append((start, i))
            start = None
    return out


def measure(samples, rate, lo, hi):
    """Frequency (mean zero crossings), the fundamental's amplitude, and the
    brightness. Both tones are 50% squares at the same pitch, so
    the output stage treats their fundamentals alike (the hardware profile's
    low-pass passes 440 Hz at 0.976) while peak-to-peak depends on how the
    filters shape the edges."""
    seg = samples[lo + rate // 10:hi - rate // 10]
    center = sum(seg) / len(seg)
    up = [i for i in range(1, len(seg)) if seg[i - 1] <= center < seg[i]]
    hz = rate * (len(up) - 1) / (up[-1] - up[0])
    span = seg[up[0]:up[-1]]
    w = 2 * math.pi * hz / rate
    re = sum((v - center) * math.cos(w * t) for t, v in enumerate(span))
    im = sum((v - center) * math.sin(w * t) for t, v in enumerate(span))
    fundamental = 2 * math.hypot(re, im) / len(span)
    # Brightness: the first difference's energy over the signal's (a spectrum
    # weighted by frequency squared), which a low-pass lowers.
    x = [v - center for v in span]
    bright = sum((x[t] - x[t - 1]) ** 2 for t in range(1, len(x))) / sum(v * v for v in x)
    return hz, fundamental, bright


def check_pcm(exe, folder, out, profile):
    rendered = []
    for mode, extra in (('native', []), ('interp', ['--interp-only'])):
        wav = folder / f'pcm_{profile}_{mode}.wav'
        run([exe, out / 'pcm.fds', '--fds-bios', out / 'bios' / 'pcm.rom', '--frames', PCM_FRAMES,
             '--fds-profile', profile, '--wav-out', wav] + extra, folder, wav.with_suffix('.log'))
        rendered.append(wav.read_bytes())
    if rendered[0] != rendered[1]:
        raise AssertionError(f'pcm {profile}: native and --interp-only PCM differ')
    rate, samples = load_pcm(folder / f'pcm_{profile}_native.wav')
    windows = tone_windows(samples, rate)
    if len(windows) != 2:
        raise AssertionError(f'pcm {profile}: expected a pulse and an FDS tone, found {len(windows)} stretches')
    (p_hz, p_pp, p_high), (f_hz, f_pp, f_high) = (measure(samples, rate, a, b) for a, b in windows)
    if abs(p_hz - fixtures.PULSE_HZ) > 1 or abs(f_hz - fixtures.FDS_HZ) > 1:
        raise AssertionError(f'pcm {profile}: pulse {p_hz:.2f} Hz (want {fixtures.PULSE_HZ:.2f}), '
                             f'FDS {f_hz:.2f} Hz (want {fixtures.FDS_HZ:.2f})')
    want = fixtures.FDS_PP[profile] / fixtures.PULSE_PP
    if profile == 'hardware':
        want /= math.hypot(1, fixtures.FDS_HZ / 2000.0)      # its 1-pole low-pass at the tone
    ratio = f_pp / p_pp
    if abs(ratio / want - 1) > 0.01:                        # measured within 0.1%
        raise AssertionError(f'pcm {profile}: FDS/pulse level {ratio:.3f}, expected {want:.3f}')
    return dict(pulse_hz=p_hz, fds_hz=f_hz, ratio=ratio, want=want, fds_high=f_high, pulse_high=p_high)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--recompiler', required=True, type=Path)
    ap.add_argument('--interp', required=True, type=Path)
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--cmake', default='cmake')
    ap.add_argument('--generator')
    ap.add_argument('--make-program')
    ap.add_argument('--c-compiler')
    ap.add_argument('--config', default='Release')
    ap.add_argument('--source', type=Path, help='runner/cyc sources to build (default: this checkout)')
    args = ap.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    source = (args.source or HERE.parents[1] / 'runner' / 'cyc').resolve()
    run([sys.executable, HERE / 'fds_audio_fixtures.py', '--out', out], out, out / 'fixtures.log')

    exe, folder = build(args, out, 'sound', source)
    image, bios = out / 'sound.fds', out / 'bios' / 'sound.rom'
    checked = []
    for align in range(4):
        traces = {}
        for mode, program, extra in [('native', exe, []), ('interp', exe, ['--interp-only']),
                                     ('standalone', args.interp.resolve(), [])]:
            stem = folder / f'a{align}_{mode}'
            cmd = [program, image, '--fds-bios', bios, '--frames', SOUND_FRAMES, '--align', align,
                   '--hash-out', stem.with_suffix('.hash')] + extra
            if mode == 'native':
                cmd += ['--ring-out', stem.with_suffix('.ring'), '--frame-log', stem.with_suffix('.frames')]
            run(cmd, folder, stem.with_suffix('.log'))
            traces[mode] = stem.with_suffix('.hash').read_text().splitlines()
        for mode in ('interp', 'standalone'):
            if traces[mode] != traces['native'] or len(traces['native']) != SOUND_FRAMES:
                i = next((i for i, (x, y) in enumerate(zip(traces['native'], traces[mode])) if x != y),
                         min(len(traces['native']), len(traces[mode])))
                raise AssertionError(f'sound align {align}: {mode} differs from native at frame {i}')
        stem = folder / f'a{align}_native'
        checked.append(check_replay(f'sound align {align}', stem.with_suffix('.ring'),
                                    stem.with_suffix('.frames'), 'mesen'))
    for profile in ('mesen2', 'hardware'):
        stem = folder / f'{profile}_native'
        run([exe, image, '--fds-bios', bios, '--frames', SOUND_FRAMES, '--fds-profile', profile,
             '--ring-out', stem.with_suffix('.ring'), '--frame-log', stem.with_suffix('.frames')],
            folder, stem.with_suffix('.log'))
        checked.append(check_replay(f'sound {profile}', stem.with_suffix('.ring'), stem.with_suffix('.frames'),
                                    'mesen2'))
    print(f'sound: native = --interp-only = cyc_interp at 4 alignments; model replay exact on '
          f'{sum(r for r, _ in checked)} reads and {sum(s for _, s in checked)} frame states '
          f'(mesen x4, mesen2, hardware); ring summaries match')

    exe, folder = build(args, out, 'pcm', source)
    results = {p: check_pcm(exe, folder, out, p) for p in ('mesen', 'hardware')}
    # The hardware profile's output filter dulls the FDS square, not the pulse.
    if not (results['hardware']['fds_high'] < 0.5 * results['mesen']['fds_high'] and
            results['hardware']['pulse_high'] == results['mesen']['pulse_high']):
        raise AssertionError(f"hardware low-pass: FDS brightness {results['hardware']['fds_high']:.4f} "
                             f"vs {results['mesen']['fds_high']:.4f} unfiltered")
    for p, r in results.items():
        print(f"pcm {p}: pulse {r['pulse_hz']:.2f} Hz, FDS {r['fds_hz']:.2f} Hz, FDS/pulse {r['ratio']:.3f} "
              f"(expected {r['want']:.3f}), brightness FDS {r['fds_high']:.4f} pulse {r['pulse_high']:.4f}")
    print('PASS')


if __name__ == '__main__':
    main()
