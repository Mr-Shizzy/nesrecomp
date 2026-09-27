"""Sunsoft FME-7 banks, $6000 window, CPU-cycle IRQ, and 5B sound programs.

Expected values come from https://www.nesdev.org/wiki/Sunsoft_FME-7 and
https://www.nesdev.org/wiki/Sunsoft_5B_audio; CHR ROM pages hold their page
number. The 5B programs are measured by test_cyc_expansion_audio.py.
"""
from mapper_fixtures import handoff
from mapper_ppu_fixtures import ppu_contract


def _cmd(command, value):
    return [('cpu', 0x8000, command), ('cpu', 0xa000, value)]


def fme7_contract():
    ops = []
    ops += _cmd(0, 0x21) + _cmd(7, 0xfe) + [('read', 0, 0x21), ('read', 0x1c00, 0xfe)]
    ops += _cmd(3, 0x80) + [('read', 0x0c00, 0x80)]
    # Mirroring 0-3: vertical, horizontal, one-screen A, one-screen B.
    ops += _cmd(12, 0) + [('write', 0x2000, 0x31), ('read', 0x2800, 0x31)]
    ops += _cmd(12, 1) + [('read', 0x2400, 0x31)]
    ops += _cmd(12, 2) + [('read', 0x2c00, 0x31)]
    ops += _cmd(12, 3) + [('write', 0x2000, 0x32)]
    ops += _cmd(12, 0) + [('read', 0x2400, 0x32), ('read', 0x2000, 0x31)]
    # $6000: ROM bank 5 (its first code byte is SEI, the vectors end it),
    # enabled RAM, and open bus with RAM selected but disabled.
    ops += _cmd(8, 0x05) + [('cpu_read', 0x6000, 0x78), ('cpu_read', 0x7ffb, 0x80)]
    ops += _cmd(8, 0xc0) + [('cpu', 0x6000, 0x5a), ('cpu_read', 0x6000, 0x5a)]
    ops += _cmd(8, 0x40) + [('cpu', 0x6000, 0x11), ('cpu_read', 0x6000, 0x60)]
    ops += _cmd(8, 0xc0) + [('cpu_read', 0x6000, 0x5a)]
    return ppu_contract(69, 128, 256, ops, '_fme7')


def fme7_irq_program(dma=False):
    """Take exactly one IRQ from a counter of $03E8; the handler acknowledges
    and disables through command 13. With dma, an OAM DMA runs while counting."""
    code = bytearray([0x78, 0xd8, 0xa2, 0xff, 0x9a])

    def store(a, v):
        code.extend([0xa9, v, 0x8d, a & 255, a >> 8])

    store(0, 0)
    store(0x4017, 0x40)
    for command, value in ((14, 0xe8), (15, 0x03), (13, 0x81)):
        store(0x8000, command)
        store(0xa000, value)
    if dma:
        store(0x4014, 2)
    code.extend([0x58, 0xa5, 0, 0xf0, 0xfc, 0xc9, 1, 0xf0, 5, 0xa9, 0xee])
    fail = 0x8000 + len(code)
    code.extend([0x4c, fail & 255, fail >> 8, 0xa9, 0x42])
    done = 0x8000 + len(code)
    code.extend([0x4c, done & 255, done >> 8])
    handler = bytes([0xa9, 13, 0x8d, 0, 0x80, 0xa9, 0, 0x8d, 0, 0xa0, 0xe6, 0, 0x40])
    prg = bytearray([0xff]) * 131072
    for bank in range(16):
        start = bank * 8192
        prg[start:start + len(code)] = code
        prg[start + 0x100:start + 0x100 + len(handler)] = handler
        prg[start + 8192 - 6:start + 8192] = bytes([0, 0x81, 0, 0x80, 0, 0x81])
    header = b'NES\x1a' + bytes([8, 1, 0x50, 0x40]) + bytes(8)
    name = 'fme7_irq' + ('_dma' if dma else '')
    return name, header + prg + bytes(8192), '00:8000\n00:8100\n', 'final:A=42'


def s5b_program(name, registers):
    """Silence the APU, write 5B registers in order, then idle."""
    code = bytearray([0x78, 0xd8])

    def store(a, v):
        code.extend([0xa9, v, 0x8d, a & 255, a >> 8])

    store(0x4015, 0)
    store(0x4017, 0x40)
    for reg, value in registers:
        store(0xc000, reg)
        store(0xe000, value)
    code.extend([0xa9, 0x42])
    done = 0x8000 + len(code)
    code.extend([0x4c, done & 255, done >> 8])
    prg = bytearray([0xff]) * 131072
    for bank in range(16):
        prg[bank * 8192:bank * 8192 + len(code)] = code
        prg[(bank + 1) * 8192 - 6:(bank + 1) * 8192] = bytes([0, 0x80]) * 3
    header = b'NES\x1a' + bytes([8, 1, 0x50, 0x40]) + bytes(8)
    return name, header + prg + bytes(8192), '00:8000\n', 'A=42'


# Measured by test_cyc_expansion_audio.py: name -> expected Hz (None = noise).
CPU_HZ = 21477272.7272727 / 12
S5B_TONES = {
    # Channel B tone, period 254: a square of CPU/(32*254).
    's5b_tone': CPU_HZ / (32 * 254),
    # Channel C holds its envelope level (tone/noise off); //// with period 14
    # is a 32-step sawtooth of CPU/(16*14*32).
    's5b_envelope': CPU_HZ / (16 * 14 * 32),
    # Channel A noise only, period 4: aperiodic, measured for level and parity.
    's5b_noise': None,
}


def s5b_fixtures():
    yield s5b_program('s5b_tone', [(7, 0x3d), (2, 254), (3, 0), (9, 15)])
    yield s5b_program('s5b_envelope', [(7, 0x3f), (8, 0), (9, 0), (10, 0x10),
                                       (11, 14), (12, 0), (13, 0x0c)])
    yield s5b_program('s5b_noise', [(7, 0x37), (6, 4), (8, 15)])


def sunsoft4_contract():
    ops = [('cpu', 0x8000, 5), ('read', 0, 10), ('read', 0x0400, 11),
           ('cpu', 0xb000, 0x7f), ('read', 0x1800, 254), ('read', 0x1c00, 255),
           ('cpu', 0xe000, 0), ('write', 0x2000, 0x31), ('read', 0x2800, 0x31),
           # CHR ROM nametables: pages $85/$86 hold their page number.
           ('cpu', 0xc000, 0x05), ('cpu', 0xd000, 0x06), ('cpu', 0xe000, 0x10),
           ('read', 0x2000, 0x85), ('read', 0x2400, 0x86), ('read', 0x2800, 0x85),
           ('read', 0x2c00, 0x86), ('read', 0x3400, 0x86),
           ('write', 0x2000, 0x44), ('read', 0x2000, 0x85),
           ('cpu', 0xe000, 0x11), ('read', 0x2400, 0x85), ('read', 0x2800, 0x86),
           ('cpu', 0xe000, 0x13), ('read', 0x2000, 0x86),
           ('cpu', 0xe000, 0x12), ('read', 0x2c00, 0x85),
           ('cpu', 0xe000, 0x00), ('read', 0x2000, 0x31),
           ('cpu', 0xe000, 0x02), ('read', 0x2c00, 0x31),
           # Work RAM enable is $F000 bit 4 (the write also selects PRG bank 0).
           ('cpu', 0xf000, 0x10), ('cpu', 0x6000, 0x5a), ('cpu_read', 0x6000, 0x5a),
           ('cpu', 0xf000, 0x00), ('cpu_read', 0x6000, 0x60),
           ('cpu', 0xf000, 0x10), ('cpu_read', 0x6000, 0x5a),
           # Render from the CHR ROM nametables.
           ('cpu', 0xe000, 0x10)]
    return ppu_contract(68, 128, 256, ops, '_sunsoft4')


def sunsoft3_irq_phase():
    """Sunsoft-3: the counter is loaded directly ($C800 high then low) and runs
    while $D800 bit 4 is set; wrapping $0000 -> $FFFF disables it and asserts.
    Each of 64 IRQs stores the low byte of a 13-cycle main-loop counter, burns
    X+1 five-cycle passes, reloads $0100 and restarts the counter, then
    acknowledges with a $8000 write (the final IRQ only acknowledges)."""
    code = bytearray([0x78, 0xd8, 0xa2, 0xff, 0x9a])

    def store(a, v):
        code.extend([0xa9, v, 0x8d, a & 255, a >> 8])

    store(0x10, 0)
    store(0x11, 0)
    store(0x4017, 0x40)
    store(0xd800, 0)
    store(0xc800, 0x01)
    store(0xc800, 0x00)
    store(0xd800, 0x10)
    code.extend([0x58, 0xe6, 0x10, 0xa5, 0x11, 0xc9, 64, 0x90, 0xf8, 0xa9, 0x42])
    done = 0x8000 + len(code)
    code.extend([0x4c, done & 255, done >> 8])
    assert len(code) < 0x100
    # PHA; TXA; PHA; LDX $11; LDA $10; STA $0300,X; INC $11; INX; loop: DEX; BNE loop;
    # LDA $11; CMP #64; BCS ack; LDA #0; STA $D800; LDA #1; STA $C800; LDA #0; STA $C800;
    # LDA #$10; STA $D800; ack: STA $8000; PLA; TAX; PLA; RTI
    restart = bytes([0xa9, 0, 0x8d, 0x00, 0xd8, 0xa9, 1, 0x8d, 0x00, 0xc8, 0xa9, 0, 0x8d, 0x00, 0xc8,
                     0xa9, 0x10, 0x8d, 0x00, 0xd8])
    handler = bytes([0x48, 0x8a, 0x48, 0xa6, 0x11, 0xa5, 0x10, 0x9d, 0x00, 0x03, 0xe6, 0x11,
                     0xe8, 0xca, 0xd0, 0xfd, 0xa5, 0x11, 0xc9, 64, 0xb0, len(restart)]) + restart +         bytes([0x8d, 0x00, 0x80, 0x68, 0xaa, 0x68, 0x40])
    prg = bytearray([0xff]) * 131072
    for bank in range(8):
        start = bank * 16384
        prg[start:start + len(code)] = code
        prg[start + 0x100:start + 0x100 + len(handler)] = handler
        prg[start + 16384 - 6:start + 16384] = bytes([0, 0x81, 0, 0x80, 0, 0x81])
    chr_rom = b''.join(bytes([page & 255]) * 1024 for page in range(8))
    header = b'NES\x1a' + bytes([8, 1, 0x30, 0x40]) + bytes(8)
    return 'sunsoft3_irq_phase', header + prg + chr_rom, '00:8000\n00:8100\n', 'final:A=42'


def sunsoft3_ack_program():
    """Only $8000 acknowledges, and $D800 resets the byte toggle. A dangling
    $C800 write ($77, high) is followed by $D800, so $00/$40 load $0040. The
    main loop counts passes until the IRQ into $13. The handler's first entry
    writes only $D800 (pause): /IRQ must stay asserted, so RTI re-enters at
    once; the second entry acknowledges with $8000. A=$42 when $12 = 2."""
    code = bytearray([0x78, 0xd8, 0xa2, 0xff, 0x9a])

    def store(a, v):
        code.extend([0xa9, v, 0x8d, a & 255, a >> 8])

    store(0x12, 0)
    store(0x13, 0)
    store(0x4017, 0x40)
    store(0xc800, 0x77)
    store(0xd800, 0)
    store(0xc800, 0x00)
    store(0xc800, 0x40)
    store(0xd800, 0x10)
    # CLI; loop: INC $13; LDA $12; BEQ loop; wait: LDA $12; CMP #2; BCC wait; BNE fail;
    # LDA #$42; done: JMP done; fail: LDA #$EE; JMP fail
    code.extend([0x58, 0xe6, 0x13, 0xa5, 0x12, 0xf0, 0xfa, 0xa5, 0x12, 0xc9, 2, 0x90, 0xfa, 0xd0, 5,
                 0xa9, 0x42])
    done = 0x8000 + len(code)
    code.extend([0x4c, done & 255, done >> 8])
    fail = 0x8000 + len(code)
    code.extend([0xa9, 0xee, 0x4c, fail & 255, fail >> 8])
    assert len(code) < 0x100
    # PHA; INC $12; LDA $12; CMP #1; BNE ack; LDA #0; STA $D800; PLA; RTI; ack: STA $8000; PLA; RTI
    handler = bytes([0x48, 0xe6, 0x12, 0xa5, 0x12, 0xc9, 1, 0xd0, 7, 0xa9, 0, 0x8d, 0x00, 0xd8, 0x68, 0x40,
                     0x8d, 0x00, 0x80, 0x68, 0x40])
    prg = bytearray([0xff]) * 131072
    for bank in range(8):
        start = bank * 16384
        prg[start:start + len(code)] = code
        prg[start + 0x100:start + 0x100 + len(handler)] = handler
        prg[start + 16384 - 6:start + 16384] = bytes([0, 0x81, 0, 0x80, 0, 0x81])
    chr_rom = b''.join(bytes([page & 255]) * 1024 for page in range(8))
    header = b'NES\x1a' + bytes([8, 1, 0x30, 0x40]) + bytes(8)
    return 'sunsoft3_ack', header + prg + chr_rom, '00:8000\n00:8100\n', 'final:A=42'


def sunsoft_fixtures():
    yield handoff('sunsoft4_prg', 68, [(0xf000, 3)], 6)
    yield handoff('sunsoft4_prg_d', 68, [(0xfabc, 0x1e)], 12, chr_kb=256)
    yield sunsoft4_contract()
    yield handoff('fme7_prg', 69, [(0x8000, 9), (0xa000, 3)], 3, chr_kb=8)
    yield handoff('fme7_prg_masked', 69, [(0x8000, 0xf9), (0xa000, 0xcb)], 11, chr_kb=8)
    yield fme7_contract()
    yield fme7_irq_program()
    yield fme7_irq_program(dma=True)
    yield from s5b_fixtures()
    yield handoff('sunsoft3_prg', 67, [(0xf800, 3)], 6)
    yield handoff('sunsoft3_prg_mirror', 67, [(0xff00, 3)], 6)               # A10-A0 ignored
    yield ppu_contract(67, 128, 128, [
        ('cpu', 0x8800, 5), ('read', 0, 10), ('read', 0x0400, 11),
        ('cpu', 0xb800, 0x21), ('read', 0x1c00, 0x43), ('cpu', 0x9000, 7), ('read', 0x0800, 2),
        ('cpu', 0xe800, 0), ('write', 0x2000, 0x31), ('write', 0x2400, 0x32), ('read', 0x2800, 0x31),
        ('cpu', 0xe800, 1), ('read', 0x2800, 0x32), ('cpu', 0xe800, 2), ('read', 0x2c00, 0x31),
        ('cpu', 0xe800, 3), ('read', 0x2000, 0x32)])
    yield sunsoft3_irq_phase()
    yield sunsoft3_ack_program()
