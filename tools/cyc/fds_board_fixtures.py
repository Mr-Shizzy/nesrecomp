#!/usr/bin/env python3
"""A synthetic FDS BIOS and disks for the RAM Adapter and drive tests.

runner/cyc/fds_machine_test.c (CTest) and tools/cyc/test_cyc_fds_runtime.py
run this program on the whole machine: it exercises the board the way the
real BIOS does, through the CPU, the IRQ line and the memory map, and leaves
its results in CPU RAM, which the tests compare with expectations derived
here from the disk bytes. No Nintendo code or data: the BIOS is assembled
below, and its identity goes into bios/disksys.toml as the real one's does.

Writes into --out:
    bios/disksys.rom, bios/disksys.toml   the program and its identity
    disk.fds                              two sides (fwNES), real CRCs
    cases.txt    one case per line: NAME DISK FRAMES OPTIONS...; followed by
                 lines "  expect ADDR VALUE" (hex) for that case

Results (CPU RAM):
    $0410 timer one-shot IRQs taken (1)       $0411 5 repeat IRQs arrived (1)
    $0412 $4030.0 after $4022 disabled it (0) $0413 $4032 & 7 before the motor starts
    $0414 $4030 & $10 after the CRC check     $0415 $5A: code copied to PRG RAM ran
    $0416 $4032 & 7 with the motor off        $0417/$0418 PRG RAM $8123/$DFFF read back
    $0419 $E000 after a write to it (the ROM byte, $78)
    $041A/$041B CIRAM through $2400/$2C00 with $4025.3 = 1 (horizontal: $11, $22)
    $041C/$041D CIRAM through $2800/$2C00 with $4025.3 = 0 (vertical: $33, $44)
    $041E $4030 & $08 with $4025.3 = 1        $041F block bytes the IRQ handler took
    $0420 $C3 when the program finished       $0421 1 if the drive never became ready
    $0500-$0537 block 1 as read through $4031, $0538/$0539 its CRC bytes
"""
import argparse
import hashlib
from pathlib import Path
import zlib

from fds_fixtures import amount, block_crc, disk_info, fds_side, fwnes, files

# ---------------------------------------------------------------- assembler
OPS = {
    ('LDA', 'imm'): 0xA9, ('LDA', 'zp'): 0xA5, ('LDA', 'abs'): 0xAD, ('LDA', 'absx'): 0xBD,
    ('LDX', 'imm'): 0xA2, ('LDX', 'zp'): 0xA6, ('LDY', 'imm'): 0xA0, ('STA', 'zp'): 0x85,
    ('STA', 'abs'): 0x8D, ('STA', 'absx'): 0x9D, ('STX', 'zp'): 0x86, ('INC', 'zp'): 0xE6,
    ('INX', ''): 0xE8, ('INY', ''): 0xC8, ('DEX', ''): 0xCA, ('DEY', ''): 0x88, ('TXA', ''): 0x8A,
    ('TAX', ''): 0xAA, ('PHA', ''): 0x48, ('PLA', ''): 0x68, ('RTI', ''): 0x40, ('RTS', ''): 0x60,
    ('SEI', ''): 0x78, ('CLI', ''): 0x58, ('CLD', ''): 0xD8, ('TXS', ''): 0x9A, ('AND', 'imm'): 0x29,
    ('CMP', 'imm'): 0xC9, ('CPX', 'imm'): 0xE0, ('CPY', 'imm'): 0xC0, ('BNE', 'rel'): 0xD0,
    ('BEQ', 'rel'): 0xF0, ('BPL', 'rel'): 0x10, ('JMP', 'abs'): 0x4C, ('JSR', 'abs'): 0x20,
    ('NOP', ''): 0xEA, ('CMP', 'zp'): 0xC5,
}
SIZE = {'': 1, 'imm': 2, 'zp': 2, 'rel': 2, 'abs': 3, 'absx': 3}


class Asm:
    def __init__(self, origin):
        self.origin, self.code, self.labels, self.fixups = origin, bytearray(), {}, []

    @property
    def pc(self):
        return self.origin + len(self.code)

    def label(self, name):
        self.labels[name] = self.pc

    def op(self, name, mode='', arg=None):
        self.code.append(OPS[(name, mode)])
        if mode == 'rel':
            self.fixups.append(('rel', len(self.code), arg))
            self.code.append(0)
        elif SIZE[mode] == 2:
            self.code.append(arg & 0xFF)
        elif SIZE[mode] == 3:
            if isinstance(arg, str):
                self.fixups.append(('abs', len(self.code), arg))
                arg = 0
            self.code += arg.to_bytes(2, 'little')

    def raw(self, data):
        self.code += bytes(data)

    def resolve(self):
        for kind, at, name in self.fixups:
            target = self.labels[name]
            if kind == 'rel':
                delta = target - (self.origin + at + 1)
                assert -128 <= delta <= 127, name
                self.code[at] = delta & 0xFF
            else:
                self.code[at:at + 2] = target.to_bytes(2, 'little')
        return bytes(self.code)


MODE, IDX, TCOUNT = 0x00, 0x01, 0x02


def wait_loop(a, name, outer):
    """About outer * 1280 CPU cycles."""
    a.op('LDX', 'imm', 0); a.op('LDY', 'imm', 0)
    a.label(name); a.op('INX'); a.op('BNE', 'rel', name); a.op('INY'); a.op('CPY', 'imm', outer)
    a.op('BNE', 'rel', name)


def ppu_write(a, addr, value):
    a.op('LDA', 'imm', addr >> 8); a.op('STA', 'abs', 0x2006)
    a.op('LDA', 'imm', addr & 0xFF); a.op('STA', 'abs', 0x2006)
    a.op('LDA', 'imm', value); a.op('STA', 'abs', 0x2007)


def ppu_read(a, addr, result):
    a.op('LDA', 'imm', addr >> 8); a.op('STA', 'abs', 0x2006)
    a.op('LDA', 'imm', addr & 0xFF); a.op('STA', 'abs', 0x2006)
    a.op('LDA', 'abs', 0x2007); a.op('LDA', 'abs', 0x2007)     # the buffered read, then the byte
    a.op('STA', 'abs', result)


def bios():
    a = Asm(0xE000)
    a.label('reset')
    a.op('SEI'); a.op('CLD'); a.op('LDX', 'imm', 0xFF); a.op('TXS')
    a.op('LDA', 'imm', 0); a.op('STA', 'abs', 0x2000); a.op('STA', 'abs', 0x2001)
    a.op('STA', 'abs', 0x4022)
    a.op('LDA', 'imm', 0xC0); a.op('STA', 'abs', 0x4017)       # no APU frame IRQ (the BIOS does the same)
    a.op('LDA', 'imm', 0x83); a.op('STA', 'abs', 0x4023)
    a.op('LDA', 'imm', 0x2E); a.op('STA', 'abs', 0x4025)       # motor off, transfer reset, read mode
    a.op('LDX', 'imm', 0); a.op('LDA', 'imm', 0)
    a.label('clear'); a.op('STA', 'absx', 0x0400); a.op('STA', 'absx', 0x0500); a.op('INX'); a.op('BNE', 'rel', 'clear')
    a.op('STA', 'zp', MODE); a.op('STA', 'zp', IDX); a.op('STA', 'zp', TCOUNT)
    # 1. timer IRQ, one-shot: exactly one IRQ.
    a.op('LDA', 'imm', 500 & 0xFF); a.op('STA', 'abs', 0x4020); a.op('LDA', 'imm', 500 >> 8); a.op('STA', 'abs', 0x4021)
    a.op('LDA', 'imm', 0x02); a.op('STA', 'abs', 0x4022); a.op('CLI')
    wait_loop(a, 'w1', 8)
    a.op('SEI'); a.op('LDA', 'zp', TCOUNT); a.op('STA', 'abs', 0x0410)
    # 2. timer IRQ, repeating: wait for 5 (with a timeout), then disable.
    a.op('LDA', 'imm', 0); a.op('STA', 'zp', TCOUNT)
    a.op('LDA', 'imm', 0x03); a.op('STA', 'abs', 0x4022); a.op('CLI')
    a.op('LDX', 'imm', 0); a.op('LDY', 'imm', 0)
    a.label('w2'); a.op('LDA', 'zp', TCOUNT); a.op('CMP', 'imm', 5); a.op('BEQ', 'rel', 'w2done')
    a.op('INX'); a.op('BNE', 'rel', 'w2'); a.op('INY'); a.op('CPY', 'imm', 40); a.op('BNE', 'rel', 'w2')
    a.op('JMP', 'abs', 'w2fail')
    a.label('w2done'); a.op('LDA', 'imm', 1); a.op('STA', 'abs', 0x0411)
    a.label('w2fail'); a.op('SEI'); a.op('LDA', 'imm', 0); a.op('STA', 'abs', 0x4022)
    a.op('LDA', 'abs', 0x4030); a.op('AND', 'imm', 1); a.op('STA', 'abs', 0x0412)
    # 3. drive status with the motor off.
    a.op('LDA', 'abs', 0x4032); a.op('AND', 'imm', 7); a.op('STA', 'abs', 0x0413)
    a.op('AND', 'imm', 1); a.op('BNE', 'rel', 'nodisk_jmp')
    # 4. read block 1 through transfer IRQs, like the BIOS: motor on, wait for
    #    ready, then CRC enable + IRQ enable; the gap's $80 raises no IRQ.
    a.op('LDA', 'imm', 1); a.op('STA', 'zp', MODE)
    a.op('LDA', 'imm', 0x2D); a.op('STA', 'abs', 0x4025)
    a.op('LDX', 'imm', 0); a.op('LDY', 'imm', 0)
    a.label('rdy'); a.op('LDA', 'abs', 0x4032); a.op('AND', 'imm', 2); a.op('BEQ', 'rel', 'ready')
    a.op('INX'); a.op('BNE', 'rel', 'rdy'); a.op('INY'); a.op('CPY', 'imm', 60); a.op('BNE', 'rel', 'rdy')
    a.op('LDA', 'imm', 1); a.op('STA', 'abs', 0x0421); a.op('JMP', 'abs', 'after_read')
    a.label('nodisk_jmp'); a.op('JMP', 'abs', 'after_read')
    a.label('ready')
    a.op('LDA', 'imm', 0xED); a.op('STA', 'abs', 0x4025); a.op('CLI')
    a.label('blk'); a.op('LDA', 'zp', IDX); a.op('CMP', 'imm', 57); a.op('BNE', 'rel', 'blk')   # 56 bytes + CRC lo
    a.op('LDA', 'imm', 0xFD); a.op('STA', 'abs', 0x4025)       # CRC transfer control (bit 4)
    a.label('crc'); a.op('LDA', 'zp', IDX); a.op('CMP', 'imm', 58); a.op('BNE', 'rel', 'crc')
    a.op('LDA', 'abs', 0x4030); a.op('AND', 'imm', 0x10); a.op('STA', 'abs', 0x0414)
    a.op('SEI'); a.op('LDA', 'zp', IDX); a.op('STA', 'abs', 0x041F)
    a.label('after_read')
    a.op('SEI'); a.op('LDA', 'imm', 0x2E); a.op('STA', 'abs', 0x4025)
    wait_loop(a, 'w3', 1)
    # 5. code copied to PRG RAM runs (on the interpreter).
    ram_code = Asm(0x6000)
    ram_code.op('LDA', 'imm', 0x5A); ram_code.op('STA', 'abs', 0x0415)
    ram_code.op('LDA', 'abs', 0x4032); ram_code.op('AND', 'imm', 7); ram_code.op('STA', 'abs', 0x0416); ram_code.op('RTS')
    body = ram_code.resolve()
    for i, b in enumerate(body):
        a.op('LDA', 'imm', b); a.op('STA', 'abs', 0x6000 + i)
    a.op('JSR', 'abs', 0x6000)
    # 6. PRG RAM to $DFFF, ROM from $E000.
    a.op('LDA', 'imm', 0x9C); a.op('STA', 'abs', 0x8123); a.op('LDA', 'imm', 0x3E); a.op('STA', 'abs', 0xDFFF)
    a.op('LDA', 'imm', 0x00); a.op('STA', 'abs', 0xE000)
    a.op('LDA', 'abs', 0x8123); a.op('STA', 'abs', 0x0417); a.op('LDA', 'abs', 0xDFFF); a.op('STA', 'abs', 0x0418)
    a.op('LDA', 'abs', 0xE000); a.op('STA', 'abs', 0x0419)
    # 7. nametable arrangement from $4025.3 (rendering off).
    a.op('LDA', 'imm', 0x2E); a.op('STA', 'abs', 0x4025)        # $4025.3 = 1: horizontal
    a.op('LDA', 'abs', 0x4030); a.op('AND', 'imm', 0x08); a.op('STA', 'abs', 0x041E)
    ppu_write(a, 0x2000, 0x11); ppu_write(a, 0x2800, 0x22)
    ppu_read(a, 0x2400, 0x041A); ppu_read(a, 0x2C00, 0x041B)
    a.op('LDA', 'imm', 0x26); a.op('STA', 'abs', 0x4025)        # $4025.3 = 0: vertical
    ppu_write(a, 0x2000, 0x33); ppu_write(a, 0x2400, 0x44)
    ppu_read(a, 0x2800, 0x041C); ppu_read(a, 0x2C00, 0x041D)
    a.op('LDA', 'imm', 0xC3); a.op('STA', 'abs', 0x0420)
    a.label('done'); a.op('JMP', 'abs', 'done')
    # IRQ: mode 0 = timer (read $4030 acknowledges), mode 1 = disk byte.
    a.label('irq')
    a.op('PHA'); a.op('LDA', 'zp', MODE); a.op('BNE', 'rel', 'diskirq')
    a.op('LDA', 'abs', 0x4030); a.op('INC', 'zp', TCOUNT); a.op('PLA'); a.op('RTI')
    a.label('diskirq')
    a.op('TXA'); a.op('PHA'); a.op('LDX', 'zp', IDX)
    a.op('LDA', 'abs', 0x4031); a.op('STA', 'absx', 0x0500); a.op('INC', 'zp', IDX)
    a.op('PLA'); a.op('TAX'); a.op('PLA'); a.op('RTI')
    a.label('nmi'); a.op('RTI')
    code = a.resolve()
    assert a.origin + len(code) < 0xFFFA
    rom = bytearray([0xFF]) * 8192
    rom[:len(code)] = code
    for vec, name in ((0x1FFA, 'nmi'), (0x1FFC, 'reset'), (0x1FFE, 'irq')):
        rom[vec:vec + 2] = a.labels[name].to_bytes(2, 'little')
    return bytes(rom)


def disks():
    sides = []
    for side, name in ((0, b'SYA'), (1, b'SYB')):
        blocks = [disk_info(name=name, side=side), amount(2)] + files(
            [(0x10, b'PROGRAM' + bytes([0x30 + side]), 0x6000, 0x40, 0), (0x11, b'PATTERN' + bytes([0x30 + side]), 0x0000, 0x20, 1)],
            seed=side)
        sides.append(fds_side(blocks))
    return fwnes(sides), sides


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    out = args.out
    (out / 'bios').mkdir(parents=True, exist_ok=True)
    rom = bios()
    (out / 'bios' / 'disksys.rom').write_bytes(rom)
    (out / 'bios' / 'disksys.toml').write_text(
        '# Synthetic test BIOS (tools/cyc/fds_board_fixtures.py), not disksys.rom.\n[program]\n'
        f'name = "cyc FDS board test BIOS"\nsize = {len(rom)}\ncrc32 = "0x{zlib.crc32(rom):08X}"\n'
        f'sha1 = "{hashlib.sha1(rom).hexdigest()}"\n', newline='\n')
    image, sides = disks()
    (out / 'disk.fds').write_bytes(image)
    info = [s[:56] for s in sides]
    lines = []

    def case(name, frames, options, expect):
        lines.append(f'{name} disk.fds {frames} {" ".join(options)}'.rstrip())
        lines.extend(f'  expect {a:04X} {v:02X}' for a, v in sorted(expect.items()))

    def common(side, status, crc_flag, mirror_bit, crc_bytes=None):
        e = {0x0410: 1, 0x0411: 1, 0x0412: 0, 0x0413: status, 0x0414: crc_flag, 0x0415: 0x5A, 0x0416: status | 2,
             0x0417: 0x9C, 0x0418: 0x3E, 0x0419: 0x78, 0x041A: 0x11, 0x041B: 0x22, 0x041C: 0x33, 0x041D: 0x44,
             0x041E: mirror_bit, 0x0420: 0xC3, 0x0421: 0}
        if side is not None:
            e[0x041F] = 58
            e.update({0x0500 + i: b for i, b in enumerate(info[side])})
            crc = block_crc(info[side]) if crc_bytes is None else crc_bytes[0] | crc_bytes[1] << 8
            e[0x0538], e[0x0539] = crc & 0xFF, crc >> 8
        return e

    # The default drive (nesref's Mesen): no bad-CRC reporting, $4030.3 open bus ($40 & 8 = 0).
    case('mesen_side0', 60, [], common(0, 2, 0, 0))
    case('mesen_side1', 60, ['--fds-boot-disk', '1'], common(1, 2, 0, 0))
    case('mesen_nodisk', 60, ['--fds-boot-disk', 'none'], common(None, 7, 0, 0))
    # Mesen's constant CRC bytes: unchecked by default, a mismatch once checked.
    case('mesen_crc_const', 60, ['--fds-crc', 'mesen'], common(0, 2, 0, 0, (0x4D, 0x62)))
    case('mesen_crc_checked', 60, ['--fds-crc', 'mesen', '--fds-crc-check'], common(0, 2, 0x10, 0, (0x4D, 0x62)))
    # Mesen2 and the hardware profile: $4030.3 mirrors $4025.3.
    case('mesen2_side0', 60, ['--fds-profile', 'mesen2'], common(0, 2, 0, 8))
    case('hardware_side0', 60, ['--fds-profile', 'hardware'], common(0, 2, 0, 8))
    case('hardware_crc_bad', 60, ['--fds-profile', 'hardware', '--fds-crc', 'mesen'], common(0, 2, 0x10, 8, (0x4D, 0x62)))
    case('hardware_protect', 60, ['--fds-profile', 'hardware', '--fds-write-protect'], common(0, 6, 0, 8))
    (out / 'cases.txt').write_text('\n'.join(lines) + '\n', newline='\n')
    print(f'fds board fixtures: {sum(1 for l in lines if not l.startswith(" "))} cases -> {out}')


if __name__ == '__main__':
    main()
