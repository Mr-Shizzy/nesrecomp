#!/usr/bin/env python3
"""FDS sound unit programs: synthetic disks for the known-answer tests
(tools/cyc/test_cyc_fds_audio.py, CTest cyc_fds_audio_test) and, with the
owner's real BIOS, for the nesref comparisons (tools/cyc/fds_audio_gates.py).

Each disk holds one program: a small interpreter at $6000 running a table of
commands, [1, reg, value] = write value to $40reg, [2, lo, hi] = sample n
times, [3, lo, hi] = wait as long without reading, [0] = idle forever. A sample reads $4090 (volume gain), $4092 (mod
gain) and $4040 (the wavetable: the sample at the wave position while writes
are disabled) and stores them at $0300,X / $0400,X / $0500,X, so the reads go
to the event ring with their cycles and to CPU RAM ($0200 counts commands).
No Nintendo code or data: the synthetic BIOS is fds_ramview_fixtures.bios();
a real-BIOS disk carries the license file the real BIOS checks for, copied
from that BIOS itself ($ED37, 224 bytes, compared by the BIOS at boot) at
generation time, never committed.

Programs:
  sound   every register and behaviour the CPU can observe: wavetable write
          enable and hold, the 12-bit pitch, the volume envelope both ways at
          two master speeds, gain above 32, the four master volumes, $4083
          halt and envelope disable, $408A = 0, the mod table (all 8 steps,
          counter wrap, reset), the mod envelope, the pitch adjustment over a
          grid of counters and gains (every rounding and wrap branch), a
          negative pitch, $4088 ignored while the mod unit runs, and $4023
          turning the sound registers off
  pcm     a 2A03 pulse tone (volume 15, 50% duty), silence, then an FDS
          square wave (full gain and master volume) at the same pitch: the
          level ratio and pitch in the rendered PCM (PULSE_HZ, FDS_HZ, FDS_PP)

Writes into --out: sound.fds and pcm.fds with their synthetic BIOSes
bios/sound.rom, bios/pcm.rom (+ .toml identity files), and with --real-bios, sound_real.fds and pcm_real.fds.
"""
import argparse
import hashlib
from pathlib import Path
import zlib

from fds_fixtures import amount, disk_info, fds_side, fwnes
from fds_ramview_fixtures import Asm, bios as synthetic_bios

KYODAKU_AT, KYODAKU_LEN = 0x0D37, 0xE0     # in disksys.rom (CRC32 5E607DCF)
PTR, CMDS = 0x10, 0x0200
SPACING = 24                                # 5 * 24 cycles between samples

# Pitch of the pcm program: wave 0x407 = 1031 steps per cycle of 65536 * 64;
# pulse period 253 = 1789772.7 / (16 * 254) Hz.
FDS_FREQ, PULSE_PERIOD = 0x407, 253
CPU_HZ = 21477272.7272727 / 12
PULSE_HZ = CPU_HZ / (16 * (PULSE_PERIOD + 1))
FDS_HZ = CPU_HZ * FDS_FREQ / (65536 * 64)
# Peak-to-peak levels in the runtime's mix units (hw_apu.c pulse_mix,
# hw_fds_audio.c): the pulse at volume 15 and the FDS square at output 63.
PULSE_PP = 95.52 / (8128.0 / 15 + 100)
FDS_PP = {'mesen': 63 * 20 / 5000.0, 'mesen2': 63 * 20 / 5000.0, 'hardware': 2.4 * PULSE_PP}


def interpreter():
    a = Asm(0x6000)
    a.label('reset')
    a.op('SEI'); a.op('CLD'); a.op('LDX', 'imm', 0xFF); a.op('TXS')
    a.op('LDA', 'imm', 0); a.op('STA', 'abs', 0x2000); a.op('STA', 'abs', 0x2001); a.op('STA', 'abs', CMDS)
    a.op('LDA', 'imm', 0xC0); a.op('STA', 'abs', 0x4017)
    a.op('LDA', 'imm', 'script', 0, 'lo'); a.op('STA', 'zp', PTR)
    a.op('LDA', 'imm', 'script', 0, 'hi'); a.op('STA', 'zp', PTR + 1)
    a.op('LDX', 'imm', 0)
    a.label('next')
    a.op('INC', 'abs', CMDS)
    a.op('LDY', 'imm', 0); a.op('LDA', 'indy', PTR)
    a.op('BEQ', 'rel', 'forever')
    a.op('CMP', 'imm', 2); a.op('BEQ', 'rel', 'sample'); a.op('BCS', 'rel', 'delay')
    # [1, reg, value]: STA $40reg through a patched absolute store
    a.op('INY'); a.op('LDA', 'indy', PTR); a.op('STA', 'abs', 'store', 1)
    a.op('INY'); a.op('LDA', 'indy', PTR)
    a.label('store'); a.op('STA', 'abs', 0x4000)
    a.op('JMP', 'abs', 'advance')
    a.label('delay')     # [3, lo, hi]: n passes of the sample loop's length, no reads
    a.op('LDA', 'imm', 0x60); a.op('STA', 'zp', 0x14)                 # RTS: 'one' returns at once
    a.op('JMP', 'abs', 'count')
    a.label('sample')    # [2, lo, hi]: n samples
    a.op('LDA', 'imm', 0xAD); a.op('STA', 'zp', 0x14)                 # LDA abs: 'one' samples
    a.label('count')
    a.op('INY'); a.op('LDA', 'indy', PTR); a.op('STA', 'zp', 0x12)
    a.op('INY'); a.op('LDA', 'indy', PTR); a.op('STA', 'zp', 0x13)
    a.label('loop')
    a.op('JSR', 'abs', 'one')
    a.op('LDA', 'zp', 0x12); a.op('BNE', 'rel', 'lo_dec'); a.op('DEC', 'zp', 0x13)
    a.label('lo_dec'); a.op('DEC', 'zp', 0x12)
    a.op('LDA', 'zp', 0x12); a.op('ORA', 'zp', 0x13); a.op('BNE', 'rel', 'loop')
    a.label('advance')
    a.op('CLC'); a.op('LDA', 'zp', PTR); a.op('ADC', 'imm', 3); a.op('STA', 'zp', PTR)
    a.op('BCC', 'rel', 'next'); a.op('INC', 'zp', PTR + 1); a.op('JMP', 'abs', 'next')
    a.label('forever'); a.op('JMP', 'abs', 'forever')                 # [0]: idle, no more events
    a.label('one')
    a.op('LDA', 'zp', 0x14); a.op('CMP', 'imm', 0x60); a.op('BEQ', 'rel', 'spaced')
    a.op('LDA', 'abs', 0x4090); a.op('STA', 'absx', 0x0300)
    a.op('LDA', 'abs', 0x4092); a.op('STA', 'absx', 0x0400)
    a.op('LDA', 'abs', 0x4040); a.op('STA', 'absx', 0x0500)
    a.op('INX')
    a.label('spaced')
    a.op('LDY', 'imm', SPACING)                         # keeps the ring to a few hundred reads a frame
    a.label('space'); a.op('DEY'); a.op('BNE', 'rel', 'space')
    a.op('RTS')
    a.label('nmi'); a.op('RTI')
    return a


def w(reg, value):
    return [(1, reg & 0xFF, value & 0xFF)]


def s(n):
    return [(2, n & 0xFF, n >> 8)]


def d(n):
    return [(3, n & 0xFF, n >> 8)]


def wave(values):
    out = w(0x89, 0x80)
    for i, v in enumerate(values):
        out += w(0x40 + i, v)
    return out


def sound_script():
    c = w(0x23, 0x83)
    c += wave([i for i in range(64)])                                  # the ramp: $4040 reads the position
    c += w(0x89, 0x00) + w(0x8A, 0xE8) + w(0x87, 0x80) + w(0x86, 0x00) + w(0x85, 0x00)
    c += w(0x80, 0xA0) + w(0x82, 0x00) + w(0x83, 0x01) + s(2500)       # gain 32, pitch $100
    # volume envelope: down, up, gain 63 (no increase, then down), fast master speed
    c += w(0x80, 0x00) + s(2500) + w(0x80, 0x40) + s(2500)
    c += w(0x80, 0xBF) + w(0x80, 0x40) + s(600) + w(0x80, 0x01) + s(2500)
    c += w(0x8A, 0x02) + w(0x80, 0x43) + s(300) + w(0x80, 0x07) + s(300) + w(0x8A, 0xE8)
    # master volume, gain above 32 at each
    for v in range(4):
        c += w(0x80, 0xA0) + w(0x89, v) + s(200) + w(0x80, 0xBF) + s(200)
    c += w(0x89, 0x00) + w(0x80, 0xA0)
    # $4083: envelopes disabled (timers reset), halt, both
    c += w(0x80, 0x00) + w(0x83, 0x41) + s(600) + w(0x83, 0x81) + s(400) + w(0x83, 0x01) + s(300)
    c += w(0x83, 0xC1) + s(300) + w(0x83, 0x01) + w(0x80, 0xA0) + s(300)
    # wavetable writes enabled: position and output hold, reads return the entry
    c += w(0x89, 0x80) + s(300) + w(0x40, 0x3F) + w(0x80, 0x9F) + s(200) + w(0x40, 0x00) + w(0x89, 0x00)
    c += w(0x80, 0xA0) + s(200)
    # master speed 0: no envelope ticks
    c += w(0x8A, 0x00) + w(0x80, 0x00) + w(0x84, 0x40) + s(1500) + w(0x8A, 0xE8)
    # mod table: every step, a reset, runs of +4 and -4 that wrap the counter
    table = [0, 1, 2, 3, 4, 5, 6, 7, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 5, 5, 5, 5, 6, 7, 1, 2]
    c += w(0x80, 0xA0) + w(0x87, 0x80)
    for v in table:
        c += w(0x88, v)
    c += w(0x85, 0x00) + w(0x84, 0xBF) + w(0x86, 0x40) + w(0x87, 0x00) + s(4000)
    c += w(0x88, 0x07)                                                 # ignored: the mod unit runs
    c += w(0x84, 0x90) + s(2000) + w(0x84, 0x81) + s(2000)
    for counter in (0x3F, 0x40, 0x7F, 0x01, 0x41):
        c += w(0x85, counter) + s(150)
    c += w(0x84, 0x45) + s(3000) + w(0x84, 0x05) + s(3000)             # the mod envelope both ways
    c += w(0x82, 0xFF) + w(0x83, 0x0F) + w(0x84, 0xBF) + s(800)        # pitch $FFF, full mod
    # the pitch adjustment with the mod unit stopped (its output still applies)
    c += w(0x87, 0x80) + w(0x82, 0xFF) + w(0x83, 0x07)
    for counter in (0x40, 0x5F, 0x6F, 0x7F, 0x00, 0x01, 0x0F, 0x10, 0x11, 0x3F):
        for gain in (0, 1, 15, 16, 17, 33, 63):
            c += w(0x85, counter) + w(0x84, 0x80 | gain) + s(12)
    # a negative pitch: freq 4 with the counter at -64, gain 63
    c += w(0x82, 0x04) + w(0x83, 0x00) + w(0x85, 0x40) + w(0x84, 0xBF) + s(600)
    c += w(0x85, 0x00) + w(0x84, 0x80) + w(0x82, 0x00) + w(0x83, 0x01) + s(300)
    # $4023.1 = 0: sound registers ignored and open bus; then on again
    c += w(0x23, 0x81) + w(0x80, 0xBF) + w(0x89, 0x03) + s(400) + w(0x23, 0x83) + s(400)
    return c


def pcm_script():
    c = w(0x23, 0x83) + w(0x89, 0x00) + w(0x87, 0x80) + w(0x80, 0x80)
    c += d(3000)                                                       # ~5 frames of silence
    c += w(0x15, 0x01) + w(0x00, 0xBF) + w(0x01, 0x08) + w(0x02, PULSE_PERIOD & 0xFF) + w(0x03, PULSE_PERIOD >> 8)
    c += d(0xFFFF)                                                     # the pulse
    c += w(0x15, 0x00) + d(0x6000)                                     # silence
    c += wave([0] * 32 + [63] * 32) + w(0x89, 0x00) + w(0x80, 0xA0)
    c += w(0x82, FDS_FREQ & 0xFF) + w(0x83, FDS_FREQ >> 8)
    c += d(0xFFFF)                                                     # the FDS square
    c += w(0x80, 0x80) + d(0x6000)
    return c


def program(script):
    a = interpreter()
    a.label('script')
    for cmd in script:
        a.raw(cmd)
    a.raw([0])
    code = a.resolve()
    assert 0x6000 + len(code) < 0xDFF6
    vectors = b''.join(a.labels[n].to_bytes(2, 'little') for n in ('nmi', 'nmi', 'nmi', 'reset', 'reset'))
    return code, vectors


def block3(n, fid, name, addr, data, ftype):
    return bytes([3, n, fid]) + name + addr.to_bytes(2, 'little') + len(data).to_bytes(2, 'little') + bytes([ftype])


def disk(table, name):
    blocks = [disk_info(name=name, side=0), amount(len(table))]
    for n, (fid, fname, addr, data, ftype) in enumerate(table):
        blocks.append(block3(n, fid, fname, addr, data, ftype))
        blocks.append(b'\x04' + data)
    return fwnes([fds_side(blocks)])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--real-bios', type=Path, help="the owner's disksys.rom: also write *_real.fds for nesref")
    args = ap.parse_args()
    out = args.out
    (out / 'bios').mkdir(parents=True, exist_ok=True)
    programs = {'sound': program(sound_script()), 'pcm': program(pcm_script())}
    # The synthetic BIOS copies table entries 0 (vectors) and 1 (main) from
    # its own ROM, so each program has its own BIOS image and identity file.
    for name, (code, vectors) in programs.items():
        table = [(0, b'VECTORS ', 0xDFF6, vectors, 0), (1, b'AUDIO   ', 0x6000, code, 0)]
        rom_n = synthetic_bios([(fid, fname, addr, data) for fid, fname, addr, data, _ in table])
        (out / f'{name}.fds').write_bytes(disk(table, b'AUD'))
        (out / 'bios' / f'{name}.rom').write_bytes(rom_n)
        (out / 'bios' / f'{name}.toml').write_text(
            '# Synthetic test BIOS (tools/cyc/fds_audio_fixtures.py), not disksys.rom.\n[program]\n'
            f'name = "cyc FDS audio test BIOS ({name})"\nsize = {len(rom_n)}\ncrc32 = "0x{zlib.crc32(rom_n):08X}"\n'
            f'sha1 = "{hashlib.sha1(rom_n).hexdigest()}"\n', newline='\n')
    if args.real_bios:
        real = args.real_bios.read_bytes()
        assert len(real) == 8192 and zlib.crc32(real) == 0x5E607DCF, 'expected disksys.rom CRC32 5E607DCF'
        kyodaku = real[KYODAKU_AT:KYODAKU_AT + KYODAKU_LEN]
        for name, (code, vectors) in programs.items():
            table = [(0, b'KYODAKU-', 0x2800, kyodaku, 2), (1, b'AUDIO   ', 0x6000, code, 0),
                     (2, b'VECTORS ', 0xDFF6, vectors, 0)]
            (out / f'{name}_real.fds').write_bytes(disk(table, b'AUD'))
    print(f'fds audio fixtures -> {out}')


if __name__ == '__main__':
    main()
