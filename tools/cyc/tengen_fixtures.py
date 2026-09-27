"""Tengen RAMBO-1 (mapper 64) and 800037 (mapper 158) fixtures (nesdev wiki,
RAMBO-1 and INES Mapper 158). Expected values come from the register
descriptions, not from an emulator."""
from mapper_fixtures import handoff
from mapper_ppu_fixtures import ppu_contract
from taito_fixtures import scanline_irq_phase


def rambo_cycle_irq_phase(latch, rewrite=False):
    """CPU-cycle IRQ mode ($C001 bit 0 = 1): an IRQ every (latch + 1) * 4 cycles
    after the first reload (latch | 1). Each of 64 IRQs stores the low byte of a
    main-loop counter that advances every 13 cycles, so the prescaler phase, the
    reload rule and the one-cycle /IRQ delay all reach $0300-$033F. With
    rewrite, the handler also writes $C001 mid-count, which must request a
    reload and restart the prescaler (the next clock 4 cycles later)."""
    code = bytearray([0x78, 0xd8, 0xa2, 0xff, 0x9a])

    def store(a, v):
        code.extend([0xa9, v, 0x8d, a & 255, a >> 8])

    store(0x10, 0)
    store(0x11, 0)
    store(0x4017, 0x40)
    store(0xc000, latch)
    store(0xc001, 1)
    store(0xe001, 0)
    # CLI; loop: INC $10; LDA $11; CMP #64; BCC loop; LDA #$42; done: JMP done
    code.extend([0x58, 0xe6, 0x10, 0xa5, 0x11, 0xc9, 64, 0x90, 0xf8, 0xa9, 0x42])
    done = 0x8000 + len(code)
    code.extend([0x4c, done & 255, done >> 8])
    assert len(code) < 0x100
    # PHA; TXA; PHA; LDX $11; LDA $10; STA $0300,X; INC $11;
    # STA $E000 (acknowledge); (CPX #63; BCS +3; STA $E001); PLA; TAX; PLA; RTI
    handler = bytes([0x48, 0x8a, 0x48, 0xa6, 0x11, 0xa5, 0x10, 0x9d, 0x00, 0x03, 0xe6, 0x11,
                     0x8d, 0x00, 0xe0, 0xe0, 63, 0xb0, 3, 0x8d, 0x01, 0xe0])
    if rewrite:
        # Burn X+1 loop passes (5 cycles each) so the $C001 write lands on every prescaler phase.
        handler += bytes([0xe8, 0xca, 0xd0, 0xfd, 0xa9, 1, 0x8d, 0x01, 0xc0])
    handler += bytes([0x68, 0xaa, 0x68, 0x40])
    prg = bytearray([0xff]) * 131072
    for bank in range(16):
        start = bank * 8192
        prg[start:start + len(code)] = code
        prg[start + 0x100:start + 0x100 + len(handler)] = handler
        prg[start + 8192 - 6:start + 8192] = bytes([0, 0x81, 0, 0x80, 0, 0x81])
    chr_rom = b''.join(bytes([page & 255]) * 1024 for page in range(8))
    header = b'NES\x1a' + bytes([8, 1, 0x00, 0x40]) + bytes(8)
    return 'rambo_cycle_irq_%d%s' % (latch, '_rewrite' if rewrite else ''), header + prg + chr_rom, '00:8000\n00:8100\n', 'final:A=42'


def tengen_fixtures():
    # R6 at $8000 (P=0); RF at $8000 once P=1.
    yield handoff('rambo_prg', 64, [(0x8000, 6), (0x8001, 3)], 3, chr_kb=8)
    yield handoff('rambo_prg_swap', 64, [(0x8000, 15), (0x8001, 5), (0x8000, 0x40)], 5, chr_kb=8)
    yield handoff('rambo158_prg', 158, [(0x8000, 6), (0x8001, 3)], 3, chr_kb=8)
    # 2 KiB mode ignores R0 bit 0; K selects R8/R9; C inverts; $A000 mirrors (64).
    yield ppu_contract(64, 128, 256, [
        ('cpu', 0x8000, 0), ('cpu', 0x8001, 0x0b), ('cpu', 0x8000, 8), ('cpu', 0x8001, 0x21),
        ('read', 0, 10), ('read', 0x0400, 11),
        ('cpu', 0x8000, 0x20), ('read', 0, 11), ('read', 0x0400, 0x21),
        ('cpu', 0x8000, 2), ('cpu', 0x8001, 0x30), ('read', 0x1000, 0x30),
        ('cpu', 0x8000, 0xa0), ('read', 0x1000, 11), ('read', 0, 0x30),
        ('cpu', 0xa000, 0), ('write', 0x2000, 0x31), ('read', 0x2800, 0x31),
        ('cpu', 0xa000, 1), ('read', 0x2400, 0x31)])
    # Mapper 158: bank bit 7 of the pattern page's register is CIRAM A10.
    yield ppu_contract(158, 128, 256, [
        ('cpu', 0x8000, 0), ('cpu', 0x8001, 0x80), ('cpu', 0x8000, 1), ('cpu', 0x8001, 0x02),
        ('write', 0x2000, 0x31), ('read', 0x2400, 0x31), ('write', 0x2800, 0x32), ('read', 0x2c00, 0x32),
        ('cpu', 0x8000, 0), ('cpu', 0x8001, 0x00), ('read', 0x2000, 0x32),
        ('cpu', 0xa000, 1), ('read', 0x2400, 0x32)])
    yield scanline_irq_phase(64)
    yield rambo_cycle_irq_phase(0)
    yield rambo_cycle_irq_phase(20)
    yield rambo_cycle_irq_phase(21)
    yield rambo_cycle_irq_phase(20, rewrite=True)
