#!/usr/bin/env python3
"""A synthetic FDS program that saves to its disk, for the disk save tests.

No Nintendo code or data: the program below stands in for the BIOS at
$E000-$FFFF (its identity goes into bios/disksys.toml as the real one's does)
and drives the drive the way the real BIOS saves a file, by polling:

  pass 1  motor on, read blocks 1, 2, 3 (file 0's header) and 4 (its 16 data
          bytes); the first data byte is a counter. Motor off.
  pass 2  unless the disk is write protected ($4032.2): motor on, read blocks
          1-3 again, and on the byte after block 3's second CRC byte switch to
          write mode (as the BIOS does, Nazo no Murasame-jou's name save), write
          GAP zero bytes, the $80 mark with CRC enabled, block code 4, the new
          data (byte i = counter + 1 + $11 * i), then the CRC under
          CRC control, back to read mode. Motor off.
  pass 3  motor on, read every block of file 0 and file 1 back with the CRC
          checked after each, as a CRC-checking drive (--fds-crc-check) would.

Every block read ends like the BIOS's: data, CRC low byte, $4025.4 (CRC
control) set, CRC high byte, then $4030.4 (bad CRC) is collected.

Results (CPU RAM):
    $0410 counter read in pass 1       $0411 counter written (= $0410 if protected)
    $0412 $4030 & $10 ORed over pass 3 $0413 1 if pass 3 read back what pass 2 wrote
    $0414 file 1's first data byte     $0415 $4032 & 4 (write protect) before pass 2
    $0416 $4030 & $10 ORed over passes 1-2
    $0420 $C3 when the program finished

Writes into --out:
    bios/disksys.rom, bios/disksys.toml   the program and its identity
    disk.fds       two sides (fwNES), file 0 counter 0; side 1 never written
    disk_raw.fds   the same sides without the header (the same disk)
    other.fds      a different disk (another game name), for foreign saves
    spin.fds       disk.fds with block 1 byte $13 = $5A: after pass 2 the program
                   keeps the motor turning (restarting it at each end of side)
    expect.txt     GAP, the positions of the blocks on side 0's stream,
                   file 1's first data byte
"""
import argparse
import hashlib
from pathlib import Path
import zlib

import fds_board_fixtures as board
from fds_board_fixtures import Asm
from fds_fixtures import LEAD_IN, BLOCK_GAP, amount, disk_info, fds_side, fwnes, header, payload

board.OPS.update({
    ('STA', 'indy'): 0x91, ('LDA', 'indy'): 0xB1, ('ORA', 'zp'): 0x05, ('ORA', 'imm'): 0x09, ('EOR', 'imm'): 0x49,
    ('CLC', ''): 0x18, ('ADC', 'imm'): 0x69, ('STY', 'zp'): 0x84, ('LDY', 'zp'): 0xA4, ('CPY', 'zp'): 0xC4,
    ('LDA', 'absy'): 0xB9, ('STA', 'absy'): 0x99, ('CMP', 'absy'): 0xD9, ('ASL', ''): 0x0A,
})
board.SIZE.update({'indy': 2, 'absy': 3})

GAP = 120                       # zero bytes pass 2 writes before the mark (the BIOS wrote 121 in Murasame)
PTR, CNT, BAD, TMP = 0x00, 0x02, 0x03, 0x04
DATA = 16                       # file 0's data bytes
NEW = 0x0530                    # the data pass 2 writes
BUF = {1: 0x0600, 2: 0x0640, 3: 0x0650, 4: 0x0660, 5: 0x0680, 6: 0x0690}   # read buffers per block


def program():
    a = Asm(0xE000)
    a.label('reset')
    a.op('SEI'); a.op('CLD'); a.op('LDX', 'imm', 0xFF); a.op('TXS')
    a.op('LDA', 'imm', 0); a.op('STA', 'abs', 0x2000); a.op('STA', 'abs', 0x2001); a.op('STA', 'abs', 0x4022)
    a.op('LDA', 'imm', 0xC0); a.op('STA', 'abs', 0x4017)
    a.op('LDA', 'imm', 0x83); a.op('STA', 'abs', 0x4023)
    a.op('LDA', 'imm', 0x2E); a.op('STA', 'abs', 0x4025)
    a.op('LDX', 'imm', 0); a.op('LDA', 'imm', 0)
    a.label('clear'); a.op('STA', 'absx', 0x0400); a.op('STA', 'absx', 0x0500); a.op('STA', 'absx', 0x0600)
    a.op('INX'); a.op('BNE', 'rel', 'clear')
    a.op('STA', 'zp', BAD)

    def motor_on():
        a.op('LDA', 'imm', 0x2D); a.op('STA', 'abs', 0x4025)
        lab = f'ready{len(a.code)}'
        a.label(lab); a.op('LDA', 'abs', 0x4032); a.op('AND', 'imm', 2); a.op('BNE', 'rel', lab)

    def motor_off():
        a.op('LDA', 'imm', 0x2E); a.op('STA', 'abs', 0x4025)
        board.wait_loop(a, f'off{len(a.code)}', 2)

    def read(block, length):
        a.op('LDA', 'imm', BUF[block] & 0xFF); a.op('STA', 'zp', PTR)
        a.op('LDA', 'imm', BUF[block] >> 8); a.op('STA', 'zp', PTR + 1)
        a.op('LDA', 'imm', length); a.op('STA', 'zp', CNT)
        a.op('JSR', 'abs', 'read_block')

    # pass 1
    motor_on()
    read(1, 56); read(2, 2); read(3, 16); read(4, 1 + DATA)
    motor_off()
    a.op('LDA', 'abs', BUF[4] + 1); a.op('STA', 'abs', 0x0410)
    a.op('CLC'); a.op('ADC', 'imm', 1); a.op('STA', 'zp', TMP)
    # new data: byte i = counter + 1 + $11 * i
    a.op('LDY', 'imm', 0)
    a.label('mk')
    a.op('LDA', 'zp', TMP); a.op('STA', 'absy', NEW)
    a.op('LDA', 'zp', TMP); a.op('CLC'); a.op('ADC', 'imm', 0x11); a.op('STA', 'zp', TMP)
    a.op('INY'); a.op('CPY', 'imm', DATA); a.op('BNE', 'rel', 'mk')
    a.op('LDA', 'abs', 0x4032); a.op('AND', 'imm', 4); a.op('STA', 'abs', 0x0415)
    a.op('BEQ', 'rel', 'writable')
    a.op('LDA', 'abs', 0x0410); a.op('STA', 'abs', 0x0411); a.op('JMP', 'abs', 'pass3')
    a.label('writable')
    a.op('LDA', 'abs', NEW); a.op('STA', 'abs', 0x0411)
    # pass 2
    motor_on()
    read(1, 56); read(2, 2); read(3, 16)
    a.op('JSR', 'abs', 'write_block')
    motor_off()
    a.op('LDA', 'zp', BAD); a.op('STA', 'abs', 0x0416)
    # spin.fds (block 1 byte $13 = $5A): keep the drive turning from here on
    a.op('LDA', 'abs', BUF[1] + 0x13); a.op('CMP', 'imm', 0x5A); a.op('BNE', 'rel', 'nospin')
    a.op('JMP', 'abs', 'spin')
    a.label('nospin')
    # pass 3
    a.label('pass3')
    a.op('LDA', 'imm', 0); a.op('STA', 'zp', BAD)
    motor_on()
    read(1, 56); read(2, 2); read(3, 16); read(4, 1 + DATA); read(5, 16); read(6, 1 + 8)
    motor_off()
    a.op('LDA', 'zp', BAD); a.op('STA', 'abs', 0x0412)
    a.op('LDA', 'abs', BUF[6] + 1); a.op('STA', 'abs', 0x0414)
    a.op('LDA', 'imm', 1); a.op('STA', 'abs', 0x0413)
    a.op('LDY', 'imm', 0)
    a.label('cmp'); a.op('LDA', 'absy', BUF[4] + 1); a.op('CMP', 'absy', NEW); a.op('BEQ', 'rel', 'same')
    a.op('LDA', 'imm', 0); a.op('STA', 'abs', 0x0413)
    a.label('same'); a.op('INY'); a.op('CPY', 'imm', DATA); a.op('BNE', 'rel', 'cmp')
    a.op('LDA', 'imm', 0xC3); a.op('STA', 'abs', 0x0420)
    a.label('done'); a.op('JMP', 'abs', 'done')

    # wait for the transfer flag ($4030.1; the read acknowledges it)
    a.label('wait_xfer')
    a.op('LDA', 'abs', 0x4030); a.op('AND', 'imm', 2); a.op('BEQ', 'rel', 'wait_xfer'); a.op('RTS')
    # read one block: gap reset, CRC on, the $80 mark, CNT bytes to (PTR), CRC
    a.label('read_block')
    a.op('LDA', 'imm', 0x2D); a.op('STA', 'abs', 0x4025)                 # CRC off for a byte: the
    a.op('LDX', 'imm', 50)                                               # drive leaves the block
    a.label('rgap'); a.op('DEX'); a.op('BNE', 'rel', 'rgap')             # (250 cycles > 150)
    a.op('LDA', 'imm', 0x6D); a.op('STA', 'abs', 0x4025)
    a.op('JSR', 'abs', 'wait_xfer'); a.op('LDA', 'abs', 0x4031)          # the mark
    a.op('LDY', 'imm', 0)
    a.label('rb'); a.op('JSR', 'abs', 'wait_xfer'); a.op('LDA', 'abs', 0x4031); a.op('STA', 'indy', PTR)
    a.op('INY'); a.op('CPY', 'zp', CNT); a.op('BNE', 'rel', 'rb')
    a.op('JSR', 'abs', 'wait_xfer'); a.op('LDA', 'abs', 0x4031)          # CRC low
    a.op('LDA', 'imm', 0x7D); a.op('STA', 'abs', 0x4025)                 # CRC control
    a.op('JSR', 'abs', 'wait_xfer'); a.op('LDA', 'abs', 0x4031)          # CRC high
    a.op('LDA', 'abs', 0x4030); a.op('AND', 'imm', 0x10); a.op('ORA', 'zp', BAD); a.op('STA', 'zp', BAD)
    a.op('RTS')
    # write block 4 of file 0 on the byte after block 3's CRC
    a.label('write_block')
    a.op('LDA', 'imm', 0); a.op('STA', 'abs', 0x4024)
    a.op('LDA', 'imm', 0x29); a.op('STA', 'abs', 0x4025)                 # write mode, CRC off: gap
    a.op('LDX', 'imm', GAP - 1)
    a.label('wg'); a.op('JSR', 'abs', 'wait_xfer'); a.op('LDA', 'imm', 0); a.op('STA', 'abs', 0x4024)
    a.op('DEX'); a.op('BNE', 'rel', 'wg')
    a.op('JSR', 'abs', 'wait_xfer'); a.op('LDA', 'imm', 0x80); a.op('STA', 'abs', 0x4024)
    a.op('LDA', 'imm', 0x69); a.op('STA', 'abs', 0x4025)                 # CRC on: the mark counts
    a.op('JSR', 'abs', 'wait_xfer'); a.op('LDA', 'imm', 0x04); a.op('STA', 'abs', 0x4024)
    a.op('LDY', 'imm', 0)
    a.label('wd'); a.op('JSR', 'abs', 'wait_xfer'); a.op('LDA', 'absy', NEW); a.op('STA', 'abs', 0x4024)
    a.op('INY'); a.op('CPY', 'imm', DATA); a.op('BNE', 'rel', 'wd')
    a.op('JSR', 'abs', 'wait_xfer')
    a.op('LDA', 'imm', 0x79); a.op('STA', 'abs', 0x4025)                 # CRC control: the CRC bytes
    board.wait_loop(a, 'wcrc', 1)                                        # ~1280 cycles: CRC + a few zeros
    a.op('LDA', 'imm', 0x2D); a.op('STA', 'abs', 0x4025)                 # read mode
    a.op('RTS')
    # restart the motor whenever the drive stops at the end of the side
    a.label('spin')
    a.op('LDA', 'imm', 0x2E); a.op('STA', 'abs', 0x4025); a.op('LDA', 'imm', 0x2D); a.op('STA', 'abs', 0x4025)
    a.label('sp1'); a.op('LDA', 'abs', 0x4032); a.op('AND', 'imm', 2); a.op('BNE', 'rel', 'sp1')
    a.label('sp2'); a.op('LDA', 'abs', 0x4032); a.op('AND', 'imm', 2); a.op('BEQ', 'rel', 'sp2')
    a.op('JMP', 'abs', 'spin')
    a.label('irq'); a.op('RTI')
    a.label('nmi'); a.op('RTI')
    code = a.resolve()
    assert a.origin + len(code) < 0xFFFA
    rom = bytearray([0xFF]) * 8192
    rom[:len(code)] = code
    for vec, name in ((0x1FFA, 'nmi'), (0x1FFC, 'reset'), (0x1FFE, 'irq')):
        rom[vec:vec + 2] = a.labels[name].to_bytes(2, 'little')
    return bytes(rom)


def side_blocks(name, side):
    data = bytes([0]) + payload(7 + side, DATA - 1)
    return [disk_info(name=name, side=side), amount(2),
            header(0, 0x10, b'SAVEDATA', 0x6000, DATA, 0), b'\x04' + data,
            header(1, 0x11, b'OTHERFIL', 0x6100, 8, 0), b'\x04' + payload(99 + side, 8)]


def stream_positions(blocks):
    """Where each block's $80 mark sits in the Mesen 0.9.9 stream."""
    pos, out = LEAD_IN, []
    for b in blocks:
        out.append(pos)
        pos += 1 + len(b) + 2 + BLOCK_GAP
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    out = args.out
    (out / 'bios').mkdir(parents=True, exist_ok=True)
    rom = program()
    (out / 'bios' / 'disksys.rom').write_bytes(rom)
    (out / 'bios' / 'disksys.toml').write_text(
        '# Synthetic saving program (tools/cyc/fds_save_fixtures.py), not disksys.rom.\n[program]\n'
        f'name = "cyc FDS save test program"\nsize = {len(rom)}\ncrc32 = "0x{zlib.crc32(rom):08X}"\n'
        f'sha1 = "{hashlib.sha1(rom).hexdigest()}"\n', newline='\n')
    blocks = [side_blocks(b'SAV', s) for s in (0, 1)]
    sides = [fds_side(b) for b in blocks]
    (out / 'disk.fds').write_bytes(fwnes(sides))
    (out / 'disk_raw.fds').write_bytes(b''.join(sides))
    (out / 'other.fds').write_bytes(fwnes([fds_side(side_blocks(b'OTH', s)) for s in (0, 1)]))
    spin = [bytearray(x) for x in sides]
    spin[0][0x13] = 0x5A
    (out / 'spin.fds').write_bytes(fwnes([bytes(x) for x in spin]))
    marks = stream_positions(blocks[0])
    (out / 'expect.txt').write_text(
        f'gap {GAP}\ndata {DATA}\nmarks {" ".join(map(str, marks))}\nfile1_first {blocks[0][5][1]}\n'
        f'initial {blocks[0][3][1:].hex()}\n', newline='\n')
    print(f'fds save fixtures -> {out}')


if __name__ == '__main__':
    main()
