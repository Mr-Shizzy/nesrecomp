#!/usr/bin/env python3
"""A synthetic FDS program and multi-side disks for the HLE tier tests.

No Nintendo code or data. The program stands in for the BIOS at $E000-$FFFF
(its identity, and its disk-ID check anchor, go into bios/hle.toml the way
bios/disksys.toml records the real one's; the real BIOS's anchor is built in,
common/nes_fds_hle.h). It behaves like the BIOS and like the games the tier
has to serve:

  idcheck (the anchor, a JSR like the real one at $E445): wait PREDELAY frames
          (the real BIOS waits ~40 before it starts the motor), fail with 1 if
          the drive is empty, else start the drive, read block 1 and compare its
          bytes 15-24 with the 10-byte ID at ($00): $FF matches anything; a
          mismatch stops the motor and fails with 7
  load    idcheck, then blocks 2-4 (the one file), motor off; A = 0 and the
          file's first byte (the side's token, $10 + side) in $0412

Boot: read the anchor's two bytes as data (not an opcode fetch: no request),
poll $4032 once a frame for BOOT_POLLS frames as the real BIOS does behind its
logo (the drive has not been used yet: no wait for the player), then load with
the real BIOS's boot ID (FF FF FF FF FF FF 00 00 FF FF: disk 1 side A). The
scenario byte (the file's second byte) picks what follows:

  1 request    load disk 1 side B, retrying every 30 frames (a game that asks
               the BIOS and shows its error)
  2 watch      wait for the disk to come out ($4032.0, polled once a frame),
               then go in, then load side B; on failure watch again (Otocky,
               Esper Dream, Nazo no Murasame-jou)
  3 none       nothing touches the drive
  4 poller     poll $4032 once a frame and never ask for a disk
  5 ambiguous  load "disk 2, any side" from a 4-side image (matches 2A and 2B)
  6 nomatch    load another game's disk
  7 multi      load disk 2 side B (side index 3) from a 4-side image
  8 selfcheck  like watch, but after the insert read block 1 itself (no ID
               check) and go back to watching unless it is side B
  9 keep       load disk 1 side A, which is in the drive
  10 shortpoll poll $4032 for 15 frames, pause 40, five times (never long
               enough to count as waiting)
  11 watch2    watch, load side B; then watch again and load side A (a second
               swap after a request starts again with a bump)
  5 and 6 give up after 3 failures.

Results (CPU RAM):
    $0410 boot token   $0411 scenario   $0412 last loaded token   $0413 failed loads
    $0414/$0415 frame counter when the scenario's load succeeded
    $0416 ejects the program saw   $0417 frames it saw the drive empty (4: poller)
    $0418 self-check rejections (8)   $0420 $C3 when the scenario finished

Writes into --out: bios/hle.rom + bios/hle.toml, disk2.fds (2 sides, scenario
per disk: disk2_<n>.fds), disk4_<n>.fds (4 sides), expect.txt.
"""
import argparse
import hashlib
from pathlib import Path
import zlib

import fds_board_fixtures as board
from fds_board_fixtures import Asm
from fds_fixtures import amount, disk_info, fds_side, fwnes, header

board.OPS.update({
    ('STA', 'indy'): 0x91, ('LDA', 'indy'): 0xB1, ('CMP', 'indy'): 0xD1, ('CMP', 'absy'): 0xD9,
    ('LDA', 'absy'): 0xB9, ('INC', 'abs'): 0xEE, ('STY', 'zp'): 0x84, ('CPY', 'zp'): 0xC4,
    ('ORA', 'zp'): 0x05, ('LDA', 'zp'): 0xA5, ('STA', 'zp'): 0x85, ('DEC', 'zp'): 0xC6,
})
board.SIZE.update({'indy': 2, 'absy': 3})

ID, PTR, CNT, TMP, FRAME = 0x00, 0x04, 0x02, 0x03, 0x10
PREDELAY = 12
BOOT_POLLS = 30
DATA = 4
BUF = {1: 0x0600, 2: 0x0640, 3: 0x0650, 4: 0x0660}
NAME = b'HLE'
OTHER = b'OTH'
SCENARIOS = {1: 'request', 2: 'watch', 3: 'none', 4: 'poller', 5: 'ambiguous', 6: 'nomatch', 7: 'multi',
             8: 'selfcheck', 9: 'keep', 10: 'shortpoll', 11: 'watch2'}
FOUR_SIDED = {5, 7}


def disk_id(name, side, disk):
    """The 10 bytes idcheck compares: block 1 bytes 15-24."""
    return disk_info(name=name, side=side, disk=disk)[15:25]


IDS = {
    'boot': bytes([0xFF] * 6 + [0, 0, 0xFF, 0xFF]),
    'b1': disk_id(NAME, 1, 0),
    'a1': disk_id(NAME, 0, 0),
    'b2': disk_id(NAME, 1, 1),
    'any2': disk_id(NAME, 0, 1)[:6] + b'\xff' + disk_id(NAME, 0, 1)[7:],
    'other': disk_id(OTHER, 1, 0),
}


def program():
    a = Asm(0xE000)
    a.label('reset')
    a.op('SEI'); a.op('CLD'); a.op('LDX', 'imm', 0xFF); a.op('TXS')
    a.op('LDA', 'imm', 0); a.op('STA', 'abs', 0x2000); a.op('STA', 'abs', 0x2001); a.op('STA', 'abs', 0x4022)
    a.op('LDA', 'imm', 0xC0); a.op('STA', 'abs', 0x4017)
    a.op('LDA', 'imm', 0x83); a.op('STA', 'abs', 0x4023)
    a.op('LDA', 'imm', 0x2E); a.op('STA', 'abs', 0x4025)
    a.op('LDX', 'imm', 0); a.op('LDA', 'imm', 0)
    a.label('clear'); a.op('STA', 'absx', 0x0400); a.op('STA', 'absx', 0x0600); a.op('STA', 'zp', FRAME)
    a.op('INX'); a.op('BNE', 'rel', 'clear')
    a.op('STA', 'zp', FRAME + 1)
    a.op('LDA', 'imm', 0x80); a.op('STA', 'abs', 0x2000)             # NMI on: the frame counter
    # the anchor read as data: two reads 4 cycles apart, not a fetch
    a.op('LDA', 'abs', 'idcheck')
    a.fixups.append(('abs+1', len(a.code) + 1, 'idcheck'))
    a.op('LDA', 'abs', 0)

    def set_id(name):
        a.fixups.append(('lo', len(a.code) + 1, 'id_' + name))
        a.op('LDA', 'imm', 0); a.op('STA', 'zp', ID)
        a.fixups.append(('hi', len(a.code) + 1, 'id_' + name))
        a.op('LDA', 'imm', 0); a.op('STA', 'zp', ID + 1)

    def wait_frames(n):
        a.op('LDY', 'imm', n)
        lab = f'wf{len(a.code)}'
        a.label(lab); a.op('JSR', 'abs', 'frame'); a.op('DEY'); a.op('BNE', 'rel', lab)

    def succeeded():
        a.op('LDA', 'zp', FRAME); a.op('STA', 'abs', 0x0414); a.op('LDA', 'zp', FRAME + 1); a.op('STA', 'abs', 0x0415)
        a.op('JMP', 'abs', 'finish')

    # boot: the BIOS's logo wait polls the drive, then the boot load
    a.op('LDX', 'imm', BOOT_POLLS)
    a.label('boot_poll'); a.op('JSR', 'abs', 'frame'); a.op('LDA', 'abs', 0x4032); a.op('DEX')
    a.op('BNE', 'rel', 'boot_poll')
    set_id('boot')
    a.op('JSR', 'abs', 'load')
    a.op('LDA', 'abs', 0x0412); a.op('STA', 'abs', 0x0410)
    a.op('LDA', 'abs', BUF[4] + 2); a.op('STA', 'abs', 0x0411)
    for n in SCENARIOS:
        a.op('CMP', 'imm', n); a.op('BNE', 'rel', f'not{n}'); a.op('JMP', 'abs', f'sc{n}'); a.label(f'not{n}')
    a.op('JMP', 'abs', 'finish')

    # 1 request: retry every 30 frames
    a.label('sc1'); set_id('b1')
    a.label('sc1_try'); a.op('JSR', 'abs', 'load'); a.op('BNE', 'rel', 'sc1_fail'); succeeded()
    a.label('sc1_fail'); a.op('INC', 'abs', 0x0413); wait_frames(30); a.op('JMP', 'abs', 'sc1_try')

    # 2 watch / 8 selfcheck
    for n in (2, 8):
        a.label(f'sc{n}')
        a.label(f'w{n}_out'); a.op('JSR', 'abs', 'frame')
        a.op('LDA', 'abs', 0x4032); a.op('AND', 'imm', 1); a.op('BEQ', 'rel', f'w{n}_out')
        a.op('INC', 'abs', 0x0416)
        a.label(f'w{n}_in'); a.op('JSR', 'abs', 'frame'); a.op('INC', 'abs', 0x0417)
        a.op('LDA', 'abs', 0x4032); a.op('AND', 'imm', 1); a.op('BNE', 'rel', f'w{n}_in')
        if n == 8:
            a.op('JSR', 'abs', 'read_header')
            a.op('LDA', 'abs', BUF[1] + 0x15); a.op('CMP', 'imm', 1); a.op('BEQ', 'rel', 'w8_ok')
            a.op('INC', 'abs', 0x0418); a.op('JMP', 'abs', 'w8_out')
            a.label('w8_ok')
        set_id('b1'); a.op('JSR', 'abs', 'load'); a.op('BNE', 'rel', f'w{n}_fail'); succeeded()
        a.label(f'w{n}_fail'); a.op('INC', 'abs', 0x0413); a.op('JMP', 'abs', f'w{n}_out')

    # 11 watch2: side B, then side A, each after the program saw the disk go out and in
    a.label('sc11')
    for step, name in ((0, 'b1'), (1, 'a1')):
        a.label(f'w11_{step}_out'); a.op('JSR', 'abs', 'frame')
        a.op('LDA', 'abs', 0x4032); a.op('AND', 'imm', 1); a.op('BEQ', 'rel', f'w11_{step}_out')
        a.op('INC', 'abs', 0x0416)
        a.label(f'w11_{step}_in'); a.op('JSR', 'abs', 'frame'); a.op('INC', 'abs', 0x0417)
        a.op('LDA', 'abs', 0x4032); a.op('AND', 'imm', 1); a.op('BNE', 'rel', f'w11_{step}_in')
        set_id(name); a.op('JSR', 'abs', 'load'); a.op('BEQ', 'rel', f'w11_{step}_ok')
        a.op('INC', 'abs', 0x0413); a.op('JMP', 'abs', f'w11_{step}_out')
        a.label(f'w11_{step}_ok')
    succeeded()

    # 10 shortpoll: 15 polling frames, 40 quiet ones, five times
    a.label('sc10'); a.op('LDA', 'imm', 5); a.op('STA', 'zp', TMP)
    a.label('sc10_round'); a.op('LDX', 'imm', 15)
    a.label('sc10_poll'); a.op('JSR', 'abs', 'frame'); a.op('LDA', 'abs', 0x4032); a.op('DEX')
    a.op('BNE', 'rel', 'sc10_poll')
    wait_frames(40)
    a.op('DEC', 'zp', TMP); a.op('BNE', 'rel', 'sc10_round')
    a.op('JMP', 'abs', 'finish')

    # 3 none: nothing, 4 poller: $4032 every frame
    a.label('sc3'); a.op('JMP', 'abs', 'finish')
    a.label('sc4')
    a.op('LDA', 'imm', 0xC3); a.op('STA', 'abs', 0x0420)
    a.label('sc4_loop'); a.op('JSR', 'abs', 'frame')
    a.op('LDA', 'abs', 0x4032); a.op('AND', 'imm', 1); a.op('BEQ', 'rel', 'sc4_loop')
    a.op('INC', 'abs', 0x0417); a.op('JMP', 'abs', 'sc4_loop')

    # 5 ambiguous, 6 nomatch: three tries; 7 multi, 9 keep: until it works
    for n, name, tries in ((5, 'any2', 3), (6, 'other', 3), (7, 'b2', 0), (9, 'a1', 0)):
        a.label(f'sc{n}'); set_id(name)
        a.label(f'sc{n}_try'); a.op('JSR', 'abs', 'load'); a.op('BNE', 'rel', f'sc{n}_fail'); succeeded()
        a.label(f'sc{n}_fail'); a.op('INC', 'abs', 0x0413)
        if tries:
            a.op('LDA', 'abs', 0x0413); a.op('CMP', 'imm', tries); a.op('BNE', 'rel', f'sc{n}_again')
            a.op('JMP', 'abs', 'finish')
            a.label(f'sc{n}_again')
        wait_frames(30); a.op('JMP', 'abs', f'sc{n}_try')

    a.label('finish'); a.op('LDA', 'imm', 0xC3); a.op('STA', 'abs', 0x0420)
    a.label('idle'); a.op('JSR', 'abs', 'frame'); a.op('JMP', 'abs', 'idle')

    # ---- the "BIOS" ----
    a.label('frame'); a.op('LDA', 'zp', FRAME)
    a.label('frame_w'); a.op('CMP', 'zp', FRAME); a.op('BEQ', 'rel', 'frame_w'); a.op('RTS')
    a.label('predelay'); wait_frames(PREDELAY); a.op('RTS')

    def motor_on():
        a.op('LDA', 'imm', 0x2E); a.op('STA', 'abs', 0x4025)
        a.op('LDA', 'imm', 0x2D); a.op('STA', 'abs', 0x4025)
        lab = f'ready{len(a.code)}'
        a.label(lab); a.op('LDA', 'abs', 0x4032); a.op('AND', 'imm', 2); a.op('BNE', 'rel', lab)

    def read(block, length):
        a.op('LDA', 'imm', BUF[block] & 0xFF); a.op('STA', 'zp', PTR)
        a.op('LDA', 'imm', BUF[block] >> 8); a.op('STA', 'zp', PTR + 1)
        a.op('LDA', 'imm', length); a.op('STA', 'zp', CNT)
        a.op('JSR', 'abs', 'read_block')

    a.label('motor_off'); a.op('LDA', 'imm', 0x2E); a.op('STA', 'abs', 0x4025); a.op('RTS')
    # the anchor: its first instruction is a JSR, like the real BIOS's $E445
    a.label('idcheck'); a.op('JSR', 'abs', 'predelay')
    a.op('LDA', 'abs', 0x4032); a.op('AND', 'imm', 1); a.op('BEQ', 'rel', 'idc_in')
    a.op('LDA', 'imm', 1); a.op('RTS')
    a.label('idc_in')
    motor_on()
    read(1, 56)
    a.op('LDY', 'imm', 0)
    a.label('idc_cmp'); a.op('LDA', 'indy', ID); a.op('CMP', 'imm', 0xFF); a.op('BEQ', 'rel', 'idc_next')
    a.op('CMP', 'absy', BUF[1] + 15); a.op('BNE', 'rel', 'idc_bad')
    a.label('idc_next'); a.op('INY'); a.op('CPY', 'imm', 10); a.op('BNE', 'rel', 'idc_cmp')
    a.op('LDA', 'imm', 0); a.op('RTS')
    a.label('idc_bad'); a.op('JSR', 'abs', 'motor_off'); a.op('LDA', 'imm', 7); a.op('RTS')
    a.label('load'); a.op('JSR', 'abs', 'idcheck'); a.op('BEQ', 'rel', 'load_ok'); a.op('RTS')
    a.label('load_ok')
    read(2, 2); read(3, 16); read(4, 1 + DATA)
    a.op('JSR', 'abs', 'motor_off')
    a.op('LDA', 'abs', BUF[4] + 1); a.op('STA', 'abs', 0x0412)
    a.op('LDA', 'imm', 0); a.op('RTS')
    # a game's own header read (no ID check, so not the anchor)
    a.label('read_header'); motor_on(); read(1, 56); a.op('JSR', 'abs', 'motor_off'); a.op('RTS')
    a.label('wait_xfer')
    a.op('LDA', 'abs', 0x4030); a.op('AND', 'imm', 2); a.op('BEQ', 'rel', 'wait_xfer'); a.op('RTS')
    a.label('read_block')
    a.op('LDA', 'imm', 0x2D); a.op('STA', 'abs', 0x4025)
    a.op('LDX', 'imm', 50)
    a.label('rgap'); a.op('DEX'); a.op('BNE', 'rel', 'rgap')
    a.op('LDA', 'imm', 0x6D); a.op('STA', 'abs', 0x4025)
    a.op('JSR', 'abs', 'wait_xfer'); a.op('LDA', 'abs', 0x4031)
    a.op('LDY', 'imm', 0)
    a.label('rb'); a.op('JSR', 'abs', 'wait_xfer'); a.op('LDA', 'abs', 0x4031); a.op('STA', 'indy', PTR)
    a.op('INY'); a.op('CPY', 'zp', CNT); a.op('BNE', 'rel', 'rb')
    a.op('JSR', 'abs', 'wait_xfer'); a.op('LDA', 'abs', 0x4031)
    a.op('LDA', 'imm', 0x7D); a.op('STA', 'abs', 0x4025)
    a.op('JSR', 'abs', 'wait_xfer'); a.op('LDA', 'abs', 0x4031)
    a.op('RTS')
    a.label('nmi'); a.op('INC', 'zp', FRAME); a.op('BNE', 'rel', 'nmi_done'); a.op('INC', 'zp', FRAME + 1)
    a.label('nmi_done'); a.op('RTI')
    a.label('irq'); a.op('RTI')
    for name, data in IDS.items():
        a.label('id_' + name); a.raw(data)
    # resolve the lo/hi immediates too
    fix = [f for f in a.fixups if f[0] in ('lo', 'hi', 'abs+1')]
    a.fixups = [f for f in a.fixups if f[0] not in ('lo', 'hi', 'abs+1')]
    code = bytearray(a.resolve())
    for kind, at, name in fix:
        v = a.labels[name]
        if kind == 'abs+1':
            code[at:at + 2] = (v + 1).to_bytes(2, 'little')
        else:
            code[at] = v & 0xFF if kind == 'lo' else v >> 8
    assert a.origin + len(code) < 0xFFFA
    rom = bytearray([0xFF]) * 8192
    rom[:len(code)] = code
    for vec, name in ((0x1FFA, 'nmi'), (0x1FFC, 'reset'), (0x1FFE, 'irq')):
        rom[vec:vec + 2] = a.labels[name].to_bytes(2, 'little')
    return bytes(rom), a.labels['idcheck']


def side(scenario, index, disk):
    data = bytes([0x10 + index, scenario]) + bytes(DATA - 2)
    return fds_side([disk_info(name=NAME, side=index & 1, disk=disk), amount(1),
                     header(0, 0x10, b'HLETEST ', 0x6000, DATA, 0), b'\x04' + data])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    out = args.out
    (out / 'bios').mkdir(parents=True, exist_ok=True)
    rom, anchor = program()
    (out / 'bios' / 'hle.rom').write_bytes(rom)
    (out / 'bios' / 'hle.toml').write_text(
        '# Synthetic HLE test program (tools/cyc/fds_hle_fixtures.py), not disksys.rom.\n[program]\n'
        f'name = "cyc FDS HLE test program"\nsize = {len(rom)}\ncrc32 = "0x{zlib.crc32(rom):08X}"\n'
        f'sha1 = "{hashlib.sha1(rom).hexdigest()}"\n'
        '# the disk-ID check anchor (common/nes_fds_hle.h): where the ID check starts, and the\n'
        '# zero-page address of the pointer to the requested 10-byte ID\n'
        f'hle_id_check = "0x{anchor:04X}"\nhle_id_pointer = "0x{ID:02X}"\n', newline='\n')
    for n in SCENARIOS:
        if n in FOUR_SIDED:
            sides = [side(n, i, i // 2) for i in range(4)]
            (out / f'disk4_{n}.fds').write_bytes(fwnes(sides))
        else:
            sides = [side(n, i, 0) for i in range(2)]
            (out / f'disk2_{n}.fds').write_bytes(fwnes(sides))
    (out / 'expect.txt').write_text(f'anchor {anchor:04X}\npredelay {PREDELAY}\n' +
                                    ''.join(f'id {k} {v.hex()}\n' for k, v in IDS.items()), newline='\n')
    print(f'fds HLE fixtures -> {out} (anchor ${anchor:04X})')


if __name__ == '__main__':
    main()
