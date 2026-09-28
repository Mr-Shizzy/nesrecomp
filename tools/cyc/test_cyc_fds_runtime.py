#!/usr/bin/env python3
"""FDS program regressions on the synthetic BIOS (no owner data; build artifacts kept).

Compiles tools/cyc/fds_board_fixtures.py's BIOS as an FDS program (NESRecomp
<disk.fds> --fds-bios, identity from its bios/disksys.toml), builds it with
runner/cyc/cyc.cmake, and for every fixture case runs the program, the program
with --interp-only and the standalone cyc_interp at all four CPU/PPU
alignments. Requires identical --hash-out traces, the case's expected CPU RAM,
and 100% of the BIOS's cycles native. Also checks that the host refuses a BIOS
whose CRC32 its identity does not name.

python tools/cyc/test_cyc_fds_runtime.py --recompiler build/compiler/Release/NESRecomp.exe \\
    --interp build/cyc/Release/cyc_interp.exe --out build/cyc-fds-regressions [--cmake ... --generator ...]
"""
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def run(cmd, cwd, log, timeout=600, ok=(0,)):
    p = subprocess.run([str(x) for x in cmd], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                       creationflags=NO_WINDOW)
    Path(log).write_text(' '.join(str(x) for x in cmd) + '\n' + p.stdout + p.stderr)
    if p.returncode not in ok:
        raise RuntimeError(f'exit {p.returncode}: {cmd}; see {log}')
    return p


def cases(path):
    out, cur = [], None
    for line in Path(path).read_text().splitlines():
        m = re.match(r'\s+expect ([0-9A-F]{4}) ([0-9A-F]{2})', line)
        if m:
            cur[4][int(m[1], 16)] = int(m[2], 16)
        elif line.strip():
            name, disk, frames, *opts = line.split()
            cur = (name, disk, int(frames), opts, {})
            out.append(cur)
    return out


def ram_from_mem(path):
    ram = bytearray(0x800)
    for line in Path(path).read_text().splitlines():
        if line.startswith('ram '):
            addr, rest = line[4:].split(':')
            base = int(addr, 16)
            for i, b in enumerate(rest.split()):
                ram[base + i] = int(b, 16)
    return ram


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--recompiler', required=True, type=Path)
    ap.add_argument('--interp', required=True, type=Path)
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--cmake', default='cmake')
    ap.add_argument('--generator')
    ap.add_argument('--config', default='Release')
    args = ap.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    here = Path(__file__).resolve().parent
    run([sys.executable, here / 'fds_board_fixtures.py', '--out', out], out, out / 'fixtures.log')
    (out / 'game.toml').write_text('[game]\noutput_prefix = "fdsboard"\ncycle_accurate = true\nfds = true\n\n'
                                   '[fds]\nimage = "disk.fds"\nbios = "bios/disksys.rom"\n', newline='\n')
    gen = out / 'generated'
    shutil.rmtree(gen, ignore_errors=True)
    p = run([args.recompiler.resolve(), '--game', 'game.toml'], out, out / 'codegen.log')
    if 'FDS RAM Adapter' not in p.stdout:
        raise AssertionError('the recompiler did not compile an FDS program; see codegen.log')
    source = here.parents[1] / 'runner/cyc'
    (out / 'CMakeLists.txt').write_text('\n'.join([
        'cmake_minimum_required(VERSION 3.20)', 'project(cyc_fds_regressions C)', 'set(CMAKE_C_STANDARD 11)',
        f'include("{source.as_posix()}/cyc.cmake")',
        'file(GLOB GEN CONFIGURE_DEPENDS "generated/*_cyc*.c")',
        'add_executable(fdsboard ${NESRECOMP_CYC_SOURCES} ${GEN})',
        'target_include_directories(fdsboard PRIVATE ${NESRECOMP_CYC_INCLUDE_DIRS})',
        'target_link_libraries(fdsboard PRIVATE ${NESRECOMP_CYC_LIBRARIES})',
        'target_compile_definitions(fdsboard PRIVATE _CRT_SECURE_NO_WARNINGS)']) + '\n')
    command = [args.cmake, '-S', out, '-B', out / 'build', f'-DCMAKE_BUILD_TYPE={args.config}']
    if args.generator:
        command += ['-G', args.generator]
    run(command, out, out / 'configure.log', timeout=900)
    run([args.cmake, '--build', out / 'build', '--config', args.config, '--parallel', '4'], out, out / 'build.log', timeout=900)
    suffix = '.exe' if NO_WINDOW else ''
    native = out / 'build' / args.config / ('fdsboard' + suffix)
    if not native.exists():
        native = out / 'build' / ('fdsboard' + suffix)

    # Identity: a BIOS whose CRC32 its .toml does not name is refused.
    wrong = out / 'wrong'
    (wrong / 'bios').mkdir(parents=True, exist_ok=True)
    rom = bytearray((out / 'bios' / 'disksys.rom').read_bytes())
    rom[0x100] ^= 0xFF
    (wrong / 'bios' / 'disksys.rom').write_bytes(rom)
    shutil.copyfile(out / 'bios' / 'disksys.toml', wrong / 'bios' / 'disksys.toml')
    for exe in (native, args.interp.resolve()):
        p = run([exe, out / 'disk.fds', '--fds-bios', wrong / 'bios' / 'disksys.rom', '--frames', '1'], out,
                wrong / f'{exe.stem}.log', ok=(2,))
        if 'not the expected FDS BIOS' not in p.stderr:
            raise AssertionError(f'{exe.name} accepted a BIOS with the wrong CRC32')

    for name, disk, frames, opts, expect in cases(out / 'cases.txt'):
        for align in range(4):
            traces = {}
            for mode, exe, extra in [('native', native, []), ('interp', native, ['--interp-only']),
                                     ('standalone', args.interp.resolve(), [])]:
                trace = out / f'{name}_a{align}_{mode}.txt'
                mem = trace.with_suffix('.mem')
                p = run([exe, out / disk, '--fds-bios', out / 'bios' / 'disksys.rom', '--frames', frames,
                         '--align', align, '--hash-out', trace, '--mem-frame', frames - 1, '--mem-out', mem] + opts + extra,
                        out, trace.with_suffix('.log'))
                traces[mode] = trace.read_text().splitlines()
                ram = ram_from_mem(mem)
                bad = [f'${a:04X}={ram[a & 0x7FF]:02X} (expected {v:02X})' for a, v in expect.items() if ram[a & 0x7FF] != v]
                if bad:
                    raise AssertionError(f'{name} {mode} align {align}: ' + ', '.join(bad[:6]))
                if mode == 'native':
                    m = re.search(r'interpreted: ROM (\d+)', p.stdout)
                    if m and int(m[1]):
                        raise AssertionError(f'{name}: {m[1]} BIOS cycles ran on the interpreter')
                    if 'PRG RAM' not in p.stdout:
                        raise AssertionError(f'{name}: the PRG RAM routine did not run interpreted')
            for mode in ('interp', 'standalone'):
                if traces[mode] != traces['native']:
                    i = next(i for i, (x, y) in enumerate(zip(traces['native'], traces[mode])) if x != y)
                    raise AssertionError(f'{name} align {align}: {mode} differs from native at frame {i}')
        print(f'{name}: expected RAM, native/interpreter/standalone parity at all four alignments')
    return 0


if __name__ == '__main__':
    sys.exit(main())
