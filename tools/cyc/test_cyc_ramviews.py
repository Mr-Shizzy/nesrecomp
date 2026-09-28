#!/usr/bin/env python3
"""Compiled RAM views never run stale code (CTest cyc_ramview_test; no owner data).

tools/cyc/fds_ramview_fixtures.py writes a synthetic FDS program whose code in
RAM rewrites itself every way a view can go stale: operands patched by the
loop that runs them, the next instruction's opcode rewritten, overlays loaded
over each other and over part of each other, code copied over its own tail,
an NMI patching the loop it interrupts, stores through (zp),Y and abs,X, and
a variable next to code (which must NOT invalidate anything).

Pass 1 compiles the disk's files (NESRecomp <disk.fds> --fds-bios, identity
from its bios/disksys.toml) and runs the program native, --interp-only and on
the standalone cyc_interp at all four CPU/PPU alignments: the --hash-out
traces (every bus access of every cycle, memories, picture, hardware state)
must be identical, CPU RAM must hold the expected results, and the run must
actually have executed RAM views, invalidated them, and left blocks after code
stores (the event ring must show no invalidation by the variable). A capture
run (--capture-log) then feeds pass 2 (--cycle-capture-file), which must be
identical the same way, compile the self-modified operands to be read at run
time, and leave no RAM instruction to the interpreter.

python tools/cyc/test_cyc_ramviews.py --recompiler build/compiler/NESRecomp.exe \\
    --interp build/cyc/cyc_interp.exe --out build/cyc-ramviews [--cmake ... --generator ... --make-program ...]
    [--source RUNNER_CYC_DIR]   # the runtime sources (default: this checkout's runner/cyc)
"""
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
FRAMES = 40


def run(cmd, cwd, log, timeout=900, ok=(0,)):
    p = subprocess.run([str(x) for x in cmd], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                       creationflags=NO_WINDOW)
    Path(log).write_text(' '.join(str(x) for x in cmd) + '\n' + p.stdout + p.stderr)
    if p.returncode not in ok:
        raise RuntimeError(f'exit {p.returncode}: {cmd}; see {log}')
    return p


def ram_from_mem(path):
    ram = bytearray(0x800)
    for line in Path(path).read_text().splitlines():
        if line.startswith('ram '):
            addr, rest = line[4:].split(':')
            base = int(addr, 16)
            for i, b in enumerate(rest.split()):
                ram[base + i] = int(b, 16)
    return ram


def stats(stdout):
    m = re.search(r'ram views: (\d+) compiled \((\d+) usable here\), (\d+) entries, (\d+) validated, (\d+) rejected, '
                  r'(\d+) invalidated by stores, (\d+) block exits after code stores, (\d+) RAM instructions interpreted',
                  stdout)
    n = re.search(r'native: ROM (\d+) \([0-9.]+%\)  RAM views (\d+)', stdout)
    if not m or not n:
        raise AssertionError('no RAM view summary in the output')
    keys = ('views', 'usable', 'entries', 'validated', 'rejected', 'invalidated', 'exits', 'interpreted')
    s = dict(zip(keys, map(int, m.groups())))
    s['native_rom'], s['native_ram'] = int(n[1]), int(n[2])
    return s


def build(args, out, name, captures, source):
    folder = out / name
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    (folder / 'game.toml').write_text('[game]\noutput_prefix = "ramview"\ncycle_accurate = true\nfds = true\n\n'
                                      '[fds]\nimage = "../disk.fds"\nbios = "../bios/disksys.rom"\n', newline='\n')
    cmd = [args.recompiler.resolve(), '--game', 'game.toml']
    if captures:
        cmd += ['--cycle-capture-file', captures]
    p = run(cmd, folder, folder / 'codegen.log')
    if 'RAM code:' not in p.stdout:
        raise AssertionError(f'{name}: the recompiler compiled no RAM code; see codegen.log')
    (folder / 'CMakeLists.txt').write_text('\n'.join([
        'cmake_minimum_required(VERSION 3.20)', 'project(cyc_ramviews C)', 'set(CMAKE_C_STANDARD 11)',
        f'include("{source.as_posix()}/cyc.cmake")',
        'file(GLOB GEN CONFIGURE_DEPENDS "generated/*_cyc*.c")',
        'add_executable(ramview ${NESRECOMP_CYC_SOURCES} ${GEN})',
        'target_include_directories(ramview PRIVATE ${NESRECOMP_CYC_INCLUDE_DIRS})',
        'target_link_libraries(ramview PRIVATE ${NESRECOMP_CYC_LIBRARIES})',
        'target_compile_definitions(ramview PRIVATE _CRT_SECURE_NO_WARNINGS)']) + '\n')
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
    for exe in (folder / 'build' / args.config / ('ramview' + suffix), folder / 'build' / ('ramview' + suffix)):
        if exe.exists():
            return exe, folder
    raise AssertionError(f'{name}: no executable built')


def check_pass(name, exe, folder, args, out, expect):
    """Parity at four alignments and the expected results; returns the align-0 native stats."""
    result = None
    for align in range(4):
        traces = {}
        for mode, program, extra in [('native', exe, []), ('interp', exe, ['--interp-only']),
                                     ('standalone', args.interp.resolve(), [])]:
            trace = folder / f'a{align}_{mode}.txt'
            mem = trace.with_suffix('.mem')
            p = run([program, out / 'disk.fds', '--fds-bios', out / 'bios' / 'disksys.rom', '--frames', FRAMES,
                     '--align', align, '--hash-out', trace, '--mem-frame', FRAMES - 1, '--mem-out', mem] + extra,
                    folder, trace.with_suffix('.log'))
            traces[mode] = trace.read_text().splitlines()
            ram = ram_from_mem(mem)
            bad = [f'${a:04X}={ram[a & 0x7FF]:02X} (expected {v:02X})' for a, v in expect.items() if ram[a & 0x7FF] != v]
            if bad:
                raise AssertionError(f'{name} {mode} align {align}: ' + ', '.join(bad))
            if mode == 'native' and align == 0:
                result = stats(p.stdout)
        for mode in ('interp', 'standalone'):
            if traces[mode] != traces['native'] or len(traces['native']) != FRAMES:
                i = next((i for i, (x, y) in enumerate(zip(traces['native'], traces[mode])) if x != y),
                         min(len(traces['native']), len(traces[mode])))
                raise AssertionError(f'{name} align {align}: {mode} differs from native at frame {i}')
    return result


def ring_events(exe, folder, out, kind):
    ring = folder / 'ring.txt'
    run([exe, out / 'disk.fds', '--fds-bios', out / 'bios' / 'disksys.rom', '--frames', FRAMES, '--ring-out', ring],
        folder, folder / 'ring.log')
    return [line.split() for line in ring.read_text().splitlines() if not line.startswith('#') and f' {kind} ' in line]


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
    args.config = args.config or 'Release'
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    here = Path(__file__).resolve().parent
    source = (args.source or here.parents[1] / 'runner' / 'cyc').resolve()
    run([sys.executable, here / 'fds_ramview_fixtures.py', '--out', out], out, out / 'fixtures.log')
    expect = {int(a, 16): int(v, 16) for a, v in (l.split() for l in (out / 'expect.txt').read_text().splitlines())}
    layout = {n: int(a, 16) for n, a in (l.split() for l in (out / 'layout.txt').read_text().splitlines())}

    # Pass 1: the disk's files only.
    exe1, f1 = build(args, out, 'pass1', None, source)
    s1 = check_pass('pass 1', exe1, f1, args, out, expect)
    print(f'pass 1: parity at 4 alignments, expected RAM; {s1}')
    if not s1['native_ram']:
        raise AssertionError('pass 1 ran no RAM view')
    if not s1['invalidated'] or not s1['exits']:
        raise AssertionError('pass 1: no view was invalidated / no block left after a code store')
    if not s1['interpreted']:
        raise AssertionError('pass 1: the copied routine should have run on the interpreter')
    def invalidating_stores(exe, folder):
        return {int(e[4].split('=')[1], 16) for e in ring_events(exe, folder, out, 'view.invalid')}

    stores = invalidating_stores(exe1, f1)
    if layout['mixed_var'] in stores:
        raise AssertionError('pass 1: a store to the variable beside code invalidated a view')
    if layout['s2imm'] + 1 not in stores:
        raise AssertionError('pass 1: the operand store at s2imm did not invalidate the view folding it')
    if not ring_events(exe1, f1, out, 'view.exit'):
        raise AssertionError('pass 1: the ring shows no view.exit')

    # Capture, then pass 2 with the capture file.
    captures = out / 'captures.txt'
    captures.unlink(missing_ok=True)
    run([exe1, out / 'disk.fds', '--fds-bios', out / 'bios' / 'disksys.rom', '--frames', FRAMES,
         '--capture-log', captures], f1, f1 / 'capture.log')
    exe2, f2 = build(args, out, 'pass2', captures, source)
    s2 = check_pass('pass 2', exe2, f2, args, out, expect)
    print(f'pass 2: parity at 4 alignments, expected RAM; {s2}')
    generated = ''.join(p.read_text() for p in (f2 / 'generated').glob('ramview_cyc_r*.c'))
    for name in ('s2imm', 's12imm', 's13ld', 's14j'):
        block = re.search(r'L_%04X: /\*[^\n]*\n[^\n]*\n[^\n]*\n[^\n]*\n([^\n]*)' % layout[name], generated)
        if not block or 'operands vary' not in block[1]:
            raise AssertionError(f'pass 2: the self-modified operand at {name} is not read at run time')
    stores = invalidating_stores(exe2, f2)
    for name, offset in (('mixed_var', 0), ('s2imm', 1), ('s12imm', 1)):
        if layout[name] + offset in stores:
            raise AssertionError(f'pass 2: the store to {name}+{offset} still invalidated a view')
    # The opcode S3 rewrites is folded (isolated, one view per content); its
    # stores must still invalidate. (S11's copy changes only an operand read
    # at run time and opcodes of views not yet validated: no invalidation.)
    if layout['s3t'] not in stores:
        raise AssertionError('pass 2: the opcode store at s3t invalidated no view')
    if not s2['exits']:
        raise AssertionError('pass 2: no block left after a code store')
    # The rewritten opcode is compiled alone (one view per content), so its
    # neighbours keep a valid view whichever instruction it holds.
    manifest = [l.split() for l in (f2 / 'generated' / 'ramview_cyc_views.txt').read_text().splitlines()
                if not l.startswith('#')]
    s3t = f'{layout["s3t"]:04X}'
    if not any(v[2] == s3t and v[3] == s3t and v[7] == '1' for v in manifest):
        raise AssertionError('pass 2: the rewritten opcode at s3t is not isolated in a view of its own')
    if s2['interpreted']:
        raise AssertionError(f'pass 2: {s2["interpreted"]} RAM instructions still ran on the interpreter')
    if s2['native_ram'] <= s1['native_ram']:
        raise AssertionError('pass 2 did not run more RAM code native than pass 1')
    print('ram views: stale code never ran; the capture loop compiled the rest')
    return 0


if __name__ == '__main__':
    sys.exit(main())
