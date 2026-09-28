#!/usr/bin/env python3
"""A synthetic FDS BIOS and disk whose code in RAM rewrites itself, for the
compiled-RAM-view tests (tools/cyc/test_cyc_ramviews.py, CTest cyc_ramview_test).

No Nintendo code: the BIOS below only copies disk files from its own ROM into
RAM with ordinary CPU stores (as the real BIOS's loader does, at a cycle-exact
cost that does not matter here) and forwards NMIs through $DFFA. The disk
holds the same files, so the recompiler compiles them at their load
addresses. The game then exercises every way a compiled view can go stale:

  $040E S0  stores through (zp),Y and abs,Y that rewrite the next
            instruction of a routine whose view is valid (DEX -> INX)  $12
  $040F S0z the same through zp and zp,X, in code in zero page       $22
  $0400 S1  a plain loop in PRG RAM                               sum 1..100 & $FF
  $0401 S2  ADC #imm whose operand the loop increments (INC abs)  sum 1..16
  $0402     the operand afterwards                                 17
  $0403 S3  a store that rewrites the opcode of the very next
            instruction (INX/DEX), eight times                     X
  $0410-$0412 S4  two overlays at $7000 (different code), loaded
            A, B, A: the view of each must be re-validated
  $0413 S5  overlay C at $7010 loaded over A's tail: A's head runs
            into C's code
  $0404 S6  a routine that increments a variable right after its
            RTS (code and data on one page), 200 calls             200
  $0405 S7  a routine copied to CPU RAM $0300 that is in no file
  $0406 S7b a disk file at CPU RAM $0580
  $0407/$0408 S8  an immediate operand patched through (zp),Y
  $0409 S9  an operand patched through abs,X
  $040A S11 a routine that copies new code over its own tail and
            falls through into it
  $0414 S13 LDA abs whose address operand the loop patches           $5C
  $0415 S14 JMP abs whose target is patched                          $22
  $040C S12 an NMI handler that increments an immediate operand of
            the loop it interrupts, 20 NMIs                        20
  $040D     the NMI count                                          20
  $04FF     $C3 when the program finished

Writes into --out: bios/disksys.rom, bios/disksys.toml, disk.fds and
expect.txt ("ADDR VALUE" hex lines), plus layout.txt (named addresses the
test checks the event ring against).
"""
import argparse
import hashlib
from pathlib import Path
import zlib

from fds_fixtures import amount, disk_info, fds_side, fwnes

OPS = {
    'ADC': {'imm': 0x69, 'zp': 0x65, 'abs': 0x6D},
    'AND': {'imm': 0x29},
    'ASL': {'': 0x0A},
    'BCC': {'rel': 0x90}, 'BCS': {'rel': 0xB0}, 'BEQ': {'rel': 0xF0}, 'BMI': {'rel': 0x30},
    'BNE': {'rel': 0xD0}, 'BPL': {'rel': 0x10},
    'CLC': {'': 0x18}, 'CLD': {'': 0xD8}, 'CLI': {'': 0x58}, 'SEC': {'': 0x38}, 'SEI': {'': 0x78},
    'CMP': {'imm': 0xC9, 'zp': 0xC5, 'abs': 0xCD}, 'CPX': {'imm': 0xE0}, 'CPY': {'imm': 0xC0},
    'DEC': {'zp': 0xC6, 'abs': 0xCE}, 'DEX': {'': 0xCA}, 'DEY': {'': 0x88},
    'EOR': {'imm': 0x49}, 'ORA': {'imm': 0x09, 'zp': 0x05},
    'INC': {'zp': 0xE6, 'abs': 0xEE}, 'INX': {'': 0xE8}, 'INY': {'': 0xC8},
    'JMP': {'abs': 0x4C, 'ind': 0x6C}, 'JSR': {'abs': 0x20},
    'LDA': {'imm': 0xA9, 'zp': 0xA5, 'abs': 0xAD, 'absx': 0xBD, 'absy': 0xB9, 'indy': 0xB1},
    'LDX': {'imm': 0xA2, 'zp': 0xA6, 'abs': 0xAE},
    'LDY': {'imm': 0xA0, 'zp': 0xA4},
    'NOP': {'': 0xEA}, 'PHA': {'': 0x48}, 'PLA': {'': 0x68}, 'RTI': {'': 0x40}, 'RTS': {'': 0x60},
    'SBC': {'imm': 0xE9},
    'STA': {'zp': 0x85, 'zpx': 0x95, 'abs': 0x8D, 'absx': 0x9D, 'absy': 0x99, 'indy': 0x91},
    'STX': {'zp': 0x86, 'abs': 0x8E}, 'STY': {'zp': 0x84},
    'TAX': {'': 0xAA}, 'TXA': {'': 0x8A}, 'TXS': {'': 0x9A}, 'TAY': {'': 0xA8}, 'TYA': {'': 0x98},
}
SIZE = {'': 1, 'imm': 2, 'zp': 2, 'zpx': 2, 'rel': 2, 'indy': 2, 'abs': 3, 'absx': 3, 'absy': 3, 'ind': 3}


class Asm:
    """Two-pass-free assembler: labels may be used before they are defined."""

    def __init__(self, origin):
        self.origin, self.code, self.labels, self.fixups = origin, bytearray(), {}, []

    @property
    def pc(self):
        return self.origin + len(self.code)

    def label(self, name):
        assert name not in self.labels, name
        self.labels[name] = self.pc

    def op(self, name, mode='', arg=None, offset=0, part='lo'):
        self.code.append(OPS[name][mode])
        n = SIZE[mode] - 1
        if mode == 'rel':
            self.fixups.append(('rel', len(self.code), arg, 0))
            self.code.append(0)
        elif n:
            if isinstance(arg, str):
                self.fixups.append(('abs' if n == 2 else part, len(self.code), arg, offset))
                arg = 0
            self.code += (arg + offset & 0xFFFF).to_bytes(2, 'little')[:n]

    def raw(self, data):
        self.code += bytes(data)

    def resolve(self, extern=None):
        extern = extern or {}
        for kind, at, name, offset in self.fixups:
            target = self.labels[name] if name in self.labels else extern[name]
            if kind == 'rel':
                delta = target - (self.origin + at + 1)
                assert -128 <= delta <= 127, name
                self.code[at] = delta & 0xFF
            elif kind == 'lo':
                self.code[at] = target + offset & 0xFF
            elif kind == 'hi':
                self.code[at] = (target + offset) >> 8 & 0xFF
            else:
                self.code[at:at + 2] = (target + offset & 0xFFFF).to_bytes(2, 'little')
        return bytes(self.code)


def routine(origin, body):
    a = Asm(origin)
    body(a)
    return a


# --------------------------------------------------------------- the files
# Zero page: $10-$15 loader pointers/count, $20-$2F game scratch.
SRC, DST, CNT = 0x10, 0x12, 0x14
BIOS_LOAD = 0xE100          # A = file index
F_VECTOR, F_MAIN, F_OVA, F_OVB, F_OVC, F_CPU, F_SMCI, F_ZP = range(8)


def ova(a):     # $7000: A = $A1 + $10, then its tail at $7010
    a.op('LDA', 'imm', 0xA1); a.op('CLC'); a.op('ADC', 'imm', 0x10); a.op('JMP', 'abs', 0x7010)
    a.raw([0xEA] * (0x10 - len(a.code)))
    a.op('STA', 'zp', 0x22); a.op('LDA', 'zp', 0x22); a.op('RTS')


def ovb(a):     # $7000: A = $B2 - $02, a different instruction layout
    a.op('SEC'); a.op('LDA', 'imm', 0xB2); a.op('SBC', 'imm', 0x02); a.op('NOP'); a.op('NOP'); a.op('RTS')


def ovc(a):     # $7010, over A's tail: A + 5
    a.op('CLC'); a.op('ADC', 'imm', 0x05); a.op('RTS')


def smci(a):    # $7100: rewrite the next instruction through (zp),Y, then abs,Y
    a.op('LDA', 'imm', 't0', 0, 'lo'); a.op('STA', 'zp', 0x28); a.op('LDA', 'imm', 't0', 0, 'hi'); a.op('STA', 'zp', 0x29)
    a.op('LDX', 'imm', 0x10); a.op('LDY', 'imm', 0); a.op('LDA', 'imm', 0xE8); a.op('STA', 'indy', 0x28)
    a.label('t0'); a.op('DEX')
    a.op('LDY', 'imm', 1); a.op('LDA', 'imm', 0xE8); a.op('STA', 'absy', 't1', -1)
    a.label('t1'); a.op('DEX')
    a.op('STX', 'abs', 0x040E); a.op('RTS')


def zpcode(a):  # $0040: the same through zp, then zp,X, in two routines (two views), code in zero page
    a.op('JSR', 'abs', 'r1'); a.op('JSR', 'abs', 'r2'); a.op('STX', 'abs', 0x040F); a.op('RTS')
    a.label('r1'); a.op('LDX', 'imm', 0x20); a.op('LDA', 'imm', 0xE8); a.op('STA', 'zp', 'ta')
    a.label('ta'); a.op('DEX'); a.op('RTS')                 # runs as INX: X = $21
    a.label('r2'); a.op('LDA', 'imm', 0xE8); a.op('STA', 'zpx', 'tb', -0x21)
    a.label('tb'); a.op('DEX'); a.op('RTS')                 # runs as INX: X = $22


def cpucode(a):     # $0580
    a.op('LDA', 'imm', 0x11); a.op('ORA', 'imm', 0x44); a.op('RTS')


COPIED = routine(0x0300, lambda a: (a.op('LDA', 'imm', 0x37), a.op('ASL'), a.op('RTS'))).resolve()
NEWTAIL = routine(0, lambda a: (a.op('LDA', 'imm', 0x99), a.op('EOR', 'imm', 0x0F), a.op('RTS'))).resolve()
PATTERN = [0xE8, 0xE8, 0xCA, 0xE8, 0xE8, 0xE8, 0xCA, 0xE8]     # INX INX DEX INX INX INX DEX INX


def main_file():
    a = Asm(0x6000)
    a.label('start')
    a.op('LDA', 'imm', 0); a.op('STA', 'zp', 0x26); a.op('STA', 'abs', 'nmi_count')
    # S0, S0z: separate files, so this file's view is still valid for S1/S2
    a.op('LDA', 'imm', F_SMCI); a.op('JSR', 'abs', BIOS_LOAD); a.op('JSR', 'abs', 0x7100)
    a.op('LDA', 'imm', F_ZP); a.op('JSR', 'abs', BIOS_LOAD); a.op('JSR', 'abs', 0x0040)
    # S1
    a.op('LDX', 'imm', 100); a.op('LDA', 'imm', 0)
    a.label('s1'); a.op('CLC'); a.op('STX', 'zp', 0x20); a.op('ADC', 'zp', 0x20); a.op('DEX'); a.op('BNE', 'rel', 's1')
    a.op('STA', 'abs', 0x0400)
    # S2: ADC #imm, the loop increments imm
    a.op('LDX', 'imm', 16); a.op('LDA', 'imm', 0); a.op('STA', 'zp', 0x21)
    a.label('s2'); a.op('CLC'); a.op('LDA', 'zp', 0x21)
    a.label('s2imm'); a.op('ADC', 'imm', 0x01)
    a.op('STA', 'zp', 0x21); a.op('INC', 'abs', 's2imm', 1); a.op('DEX'); a.op('BNE', 'rel', 's2')
    a.op('LDA', 'zp', 0x21); a.op('STA', 'abs', 0x0401); a.op('LDA', 'abs', 's2imm', 1); a.op('STA', 'abs', 0x0402)
    # S3: rewrite the next instruction's opcode
    a.op('LDX', 'imm', 0); a.op('LDY', 'imm', 0)
    a.label('s3'); a.op('LDA', 'absy', 'pattern'); a.op('STA', 'abs', 's3t')
    a.label('s3t'); a.op('DEX')
    a.op('INY'); a.op('CPY', 'imm', len(PATTERN)); a.op('BNE', 'rel', 's3')
    a.op('STX', 'abs', 0x0403)
    # S4, S5: overlays
    for fid, out in ((F_OVA, 0x0410), (F_OVB, 0x0411), (F_OVA, 0x0412), (F_OVC, 0x0413)):
        a.op('LDA', 'imm', fid); a.op('JSR', 'abs', BIOS_LOAD); a.op('JSR', 'abs', 0x7000); a.op('STA', 'abs', out)
    # S6: code and data on one page
    a.op('LDX', 'imm', 200)
    a.label('s6'); a.op('JSR', 'abs', 'mixed'); a.op('DEX'); a.op('BNE', 'rel', 's6')
    a.op('LDA', 'abs', 'mixed_var'); a.op('STA', 'abs', 0x0404)
    # S7: copy a routine to CPU RAM $0300; S7b: a disk file at $0580
    a.op('LDX', 'imm', 0)
    a.label('s7'); a.op('LDA', 'absx', 'copied'); a.op('STA', 'absx', 0x0300); a.op('INX')
    a.op('CPX', 'imm', len(COPIED)); a.op('BNE', 'rel', 's7')
    a.op('JSR', 'abs', 0x0300); a.op('STA', 'abs', 0x0405)
    a.op('LDA', 'imm', F_CPU); a.op('JSR', 'abs', BIOS_LOAD); a.op('JSR', 'abs', 0x0580); a.op('STA', 'abs', 0x0406)
    # S8: patch through (zp),Y
    a.op('LDA', 'imm', 'p8', 1); a.op('STA', 'zp', 0x24); a.op('LDA', 'imm', 'p8', 1, 'hi'); a.op('STA', 'zp', 0x25)
    a.op('LDY', 'imm', 0); a.op('LDA', 'imm', 0x5C); a.op('STA', 'indy', 0x24); a.op('JSR', 'abs', 'p8')
    a.op('STA', 'abs', 0x0407)
    a.op('LDA', 'imm', 0x3D); a.op('STA', 'indy', 0x24); a.op('JSR', 'abs', 'p8'); a.op('STA', 'abs', 0x0408)
    # S9: patch through abs,X
    a.op('LDX', 'imm', 1); a.op('LDA', 'imm', 0x42); a.op('STA', 'absx', 'p9'); a.op('JSR', 'abs', 'p9')
    a.op('STA', 'abs', 0x0409)
    # S11: copy new code over this routine's own tail and fall into it
    a.op('JSR', 'abs', 'so'); a.op('STA', 'abs', 0x040A)
    # S13: LDA abs whose address operand the loop patches ($04E0+k = 3k+1)
    a.op('LDX', 'imm', 0)
    a.label('s13i'); a.op('TXA'); a.op('ASL'); a.op('STA', 'zp', 0x2B); a.op('TXA'); a.op('CLC'); a.op('ADC', 'zp', 0x2B)
    a.op('ADC', 'imm', 1); a.op('STA', 'absx', 0x04E0); a.op('INX'); a.op('CPX', 'imm', 8); a.op('BNE', 'rel', 's13i')
    a.op('LDA', 'imm', 0); a.op('STA', 'zp', 0x2A); a.op('LDX', 'imm', 0)
    a.label('s13'); a.op('TXA'); a.op('CLC'); a.op('ADC', 'imm', 0xE0); a.op('STA', 'abs', 's13ld', 1)
    a.label('s13ld'); a.op('LDA', 'abs', 0x04E0)
    a.op('CLC'); a.op('ADC', 'zp', 0x2A); a.op('STA', 'zp', 0x2A); a.op('INX'); a.op('CPX', 'imm', 8); a.op('BNE', 'rel', 's13')
    a.op('LDA', 'zp', 0x2A); a.op('STA', 'abs', 0x0414)
    # S14: JMP abs whose target is patched
    a.op('LDA', 'imm', 's14b', 0, 'lo'); a.op('STA', 'abs', 's14j', 1)
    a.op('LDA', 'imm', 's14b', 0, 'hi'); a.op('STA', 'abs', 's14j', 2)
    a.label('s14j'); a.op('JMP', 'abs', 's14a')
    a.label('s14a'); a.op('LDA', 'imm', 0x11); a.op('JMP', 'abs', 's14done')
    a.label('s14b'); a.op('LDA', 'imm', 0x22)
    a.label('s14done'); a.op('STA', 'abs', 0x0415)
    # S12: NMIs patch the loop they interrupt
    a.op('LDA', 'imm', 0x80); a.op('STA', 'abs', 0x2000)
    a.label('s12'); a.op('LDA', 'zp', 0x26); a.op('CLC')
    a.label('s12imm'); a.op('ADC', 'imm', 0x00)
    a.op('STA', 'zp', 0x26); a.op('LDA', 'abs', 'nmi_count'); a.op('CMP', 'imm', 20); a.op('BNE', 'rel', 's12')
    a.op('LDA', 'imm', 0); a.op('STA', 'abs', 0x2000)
    a.op('LDA', 'zp', 0x26); a.op('STA', 'abs', 0x040B); a.op('LDA', 'abs', 's12imm', 1); a.op('STA', 'abs', 0x040C)
    a.op('LDA', 'abs', 'nmi_count'); a.op('STA', 'abs', 0x040D)
    a.op('LDA', 'imm', 0xC3); a.op('STA', 'abs', 0x04FF)
    a.label('done'); a.op('JMP', 'abs', 'done')
    # --- routines and data (same file, next to the code above) ---
    a.label('nmi'); a.op('PHA'); a.op('INC', 'abs', 'nmi_count'); a.op('INC', 'abs', 's12imm', 1); a.op('PLA'); a.op('RTI')
    a.label('nmi_count'); a.raw([0])
    a.label('mixed'); a.op('INC', 'abs', 'mixed_var'); a.op('RTS')
    a.label('mixed_var'); a.raw([0])
    a.label('p8'); a.op('LDA', 'imm', 0x00); a.op('RTS')
    a.label('p9'); a.op('LDX', 'imm', 0x00); a.op('TXA'); a.op('RTS')
    a.label('so'); a.op('LDX', 'imm', 0)
    a.label('so_copy'); a.op('LDA', 'absx', 'newtail'); a.op('STA', 'absx', 'so_tail'); a.op('INX')
    a.op('CPX', 'imm', len(NEWTAIL)); a.op('BNE', 'rel', 'so_copy')
    a.label('so_tail'); a.op('LDA', 'imm', 0x01); a.op('NOP'); a.op('NOP'); a.op('RTS')
    a.label('pattern'); a.raw(PATTERN)
    a.label('copied'); a.raw(COPIED)
    a.label('newtail'); a.raw(NEWTAIL)
    return a


def vector_file(main):
    # $DFF6: NMI vectors 1-3 (the BIOS forwards through $DFFA), reset, IRQ.
    nmi, start = main.labels['nmi'], main.labels['start']
    return b''.join(v.to_bytes(2, 'little') for v in (nmi, nmi, nmi, start, start))


def files():
    main = main_file()
    main_bytes = main.resolve()
    return main, [
        (F_VECTOR, b'VECTORS ', 0xDFF6, vector_file(main)),
        (F_MAIN, b'MAIN    ', 0x6000, main_bytes),
        (F_OVA, b'OVERLAYA', 0x7000, routine(0x7000, ova).resolve()),
        (F_OVB, b'OVERLAYB', 0x7000, routine(0x7000, ovb).resolve()),
        (F_OVC, b'OVERLAYC', 0x7010, routine(0x7010, ovc).resolve()),
        (F_CPU, b'CPUCODE ', 0x0580, routine(0x0580, cpucode).resolve()),
        (F_SMCI, b'SMCINDIR', 0x7100, routine(0x7100, smci).resolve()),
        (F_ZP, b'ZPCODE  ', 0x0040, routine(0x0040, zpcode).resolve()),
    ]


def bios(table):
    a = Asm(0xE000)
    a.label('reset')
    a.op('SEI'); a.op('CLD'); a.op('LDX', 'imm', 0xFF); a.op('TXS')
    a.op('LDA', 'imm', 0); a.op('STA', 'abs', 0x2000); a.op('STA', 'abs', 0x2001)
    a.op('LDA', 'imm', 0xC0); a.op('STA', 'abs', 0x4017)          # no APU frame IRQ
    a.op('LDX', 'imm', 0); a.op('LDA', 'imm', 0)
    a.label('clear'); a.op('STA', 'absx', 0x0400); a.op('INX'); a.op('BNE', 'rel', 'clear')
    a.op('LDA', 'imm', F_VECTOR); a.op('JSR', 'abs', 'load')
    a.op('LDA', 'imm', F_MAIN); a.op('JSR', 'abs', 'load')
    a.op('JMP', 'ind', 0xDFFC)
    a.label('nmi'); a.op('JMP', 'ind', 0xDFFA)
    a.label('irq'); a.op('RTI')
    a.raw([0xEA] * (BIOS_LOAD - a.pc))
    # load: A = file index; copies the file from the table below.
    a.label('load')
    a.op('ASL'); a.op('ASL'); a.op('ASL'); a.op('TAX')
    for k in range(6):
        a.op('LDA', 'absx', 'table', k); a.op('STA', 'zp', SRC + k)
    a.op('LDY', 'imm', 0)
    a.label('copy')
    a.op('LDA', 'indy', SRC); a.op('STA', 'indy', DST)
    a.op('INC', 'zp', SRC); a.op('BNE', 'rel', 'c1'); a.op('INC', 'zp', SRC + 1)
    a.label('c1'); a.op('INC', 'zp', DST); a.op('BNE', 'rel', 'c2'); a.op('INC', 'zp', DST + 1)
    a.label('c2'); a.op('LDA', 'zp', CNT); a.op('BNE', 'rel', 'c3'); a.op('DEC', 'zp', CNT + 1)
    a.label('c3'); a.op('DEC', 'zp', CNT); a.op('LDA', 'zp', CNT); a.op('ORA', 'zp', CNT + 1)
    a.op('BNE', 'rel', 'copy')
    a.op('RTS')
    # file table: src, dst, count (8 bytes per file), then the file bytes
    a.label('table')
    blobs = bytearray()
    data_at = a.pc + 8 * len(table)
    for fid, _, addr, data in table:
        a.raw((data_at + len(blobs)).to_bytes(2, 'little') + addr.to_bytes(2, 'little') +
              len(data).to_bytes(2, 'little') + bytes(2))
        blobs += data
    a.raw(blobs)
    code = a.resolve()
    assert a.origin + len(code) < 0xFFFA, hex(a.origin + len(code))
    rom = bytearray([0xFF]) * 8192
    rom[:len(code)] = code
    for vec, name in ((0x1FFA, 'nmi'), (0x1FFC, 'reset'), (0x1FFE, 'irq')):
        rom[vec:vec + 2] = a.labels[name].to_bytes(2, 'little')
    return bytes(rom)


def disk(table):
    blocks = [disk_info(name=b'RVW', side=0), amount(len(table))]
    for n, (fid, name, addr, data) in enumerate(table):
        blocks.append(bytes([3, n, fid]) + name + addr.to_bytes(2, 'little') + len(data).to_bytes(2, 'little') + b'\x00')
        blocks.append(b'\x04' + data)
    return fwnes([fds_side(blocks)])


def expected(main):
    x = 0
    for op in PATTERN:
        x = (x + (1 if op == 0xE8 else -1)) & 0xFF
    return {
        0x0400: sum(range(1, 101)) & 0xFF, 0x0401: sum(range(1, 17)) & 0xFF, 0x0402: 17, 0x0403: x,
        0x0410: 0xB1, 0x0411: 0xB0, 0x0412: 0xB1, 0x0413: 0xB6, 0x0404: 200, 0x0405: 0x6E, 0x0406: 0x55,
        0x0407: 0x5C, 0x0408: 0x3D, 0x0409: 0x42, 0x040A: 0x96, 0x040C: 20, 0x040D: 20, 0x04FF: 0xC3,
        0x040E: 0x12, 0x040F: 0x22, 0x0414: sum(3 * k + 1 for k in range(8)) & 0xFF, 0x0415: 0x22,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    out = args.out
    (out / 'bios').mkdir(parents=True, exist_ok=True)
    main_asm, table = files()
    rom = bios(table)
    (out / 'bios' / 'disksys.rom').write_bytes(rom)
    (out / 'bios' / 'disksys.toml').write_text(
        '# Synthetic test BIOS (tools/cyc/fds_ramview_fixtures.py), not disksys.rom.\n[program]\n'
        f'name = "cyc RAM view test BIOS"\nsize = {len(rom)}\ncrc32 = "0x{zlib.crc32(rom):08X}"\n'
        f'sha1 = "{hashlib.sha1(rom).hexdigest()}"\n', newline='\n')
    (out / 'disk.fds').write_bytes(disk(table))
    (out / 'expect.txt').write_text(''.join(f'{a:04X} {v:02X}\n' for a, v in sorted(expected(main_asm).items())),
                                    newline='\n')
    names = ['mixed_var', 's2imm', 's3t', 's12imm', 'so_tail', 'p8', 'p9', 's13ld', 's14j']
    (out / 'layout.txt').write_text(''.join(f'{n} {main_asm.labels[n]:04X}\n' for n in names), newline='\n')
    print(f'ram view fixtures: {len(table)} files -> {out}')


if __name__ == '__main__':
    main()
