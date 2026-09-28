#!/usr/bin/env python3
"""Synthetic Famicom Disk System images for runner/cyc/fds_disk_test.c.

Writes images (fwNES, raw and .qd forms of the same disks, plus damaged ones)
and the drive streams expected from them into --out, with manifest.txt:

    stream <image> <side> <mesen099|mesen2> <computed|mesen> <expected.bin>

The expected streams come from a transliteration of the Mesen loaders written
here independently of common/nes_fds.h:
    Mesen 0.9.9 (the nesref core) Core/FdsLoader.cpp:13-45 AddGaps
    Mesen2 b9fa69d Core/NES/Loaders/FdsLoader.cpp:26-91 AddGaps
and CRCs come from the standard library (binascii.crc_hqx on bit-reversed
bytes gives CRC-16/KERMIT). Where Mesen reads outside its buffer the
transliteration uses the header's defined rule: bytes outside the side data
read as zero and 0.9.9 stops before a block that runs past the file.
No commercial data: every byte is generated.
"""
import argparse
import binascii
from pathlib import Path

SIDE = 65500
QD_SIDE = 65536
LEAD_IN = 28300 // 8     # FdsLoader.cpp:16
BLOCK_GAP = 976 // 8     # FdsLoader.cpp:40


def rev8(b):
    return int(f'{b:08b}'[::-1], 2)


def kermit(data):
    """CRC-16/KERMIT via the stdlib's CRC-CCITT: reflect bytes in, result out."""
    c = binascii.crc_hqx(bytes(rev8(b) for b in data), 0)
    return int(f'{c:016b}'[::-1], 2)


assert kermit(b'123456789') == 0x2189   # the catalogued check value


def block_crc(block):
    return kermit(b'\x80' + block)      # the gap's final 1 bit is included


def disk_info(name=b'TST', side=0, disk=0, boot=0x0F, licensee=0x01, revision=0, signature=b'*NINTENDO-HVC*'):
    b = bytearray(56)
    b[0] = 1
    b[1:15] = signature
    b[0x0F] = licensee
    b[0x10:0x13] = name
    b[0x13] = 0x20
    b[0x14] = revision
    b[0x15] = side
    b[0x16] = disk
    b[0x17] = 0
    b[0x19] = boot
    b[0x1A:0x1F] = b'\xff' * 5
    b[0x1F:0x22] = b'\x61\x07\x23'
    b[0x22] = 0x49
    b[0x23] = 0x61
    b[0x26] = 0x02
    b[0x2C:0x2F] = b'\x61\x07\x23'
    b[0x30] = 0x80
    b[0x31:0x33] = b'\x12\x34'
    b[0x33] = 0x07
    b[0x34] = 0x02
    b[0x35] = side
    b[0x37] = 0
    return bytes(b)


def amount(n):
    return bytes([2, n])


def header(number, fid, name, addr, size, ftype):
    assert len(name) == 8
    return bytes([3, number, fid]) + name + addr.to_bytes(2, 'little') + size.to_bytes(2, 'little') + bytes([ftype])


def payload(seed, size):
    x = seed * 2654435761 & 0xFFFFFFFF
    out = bytearray()
    for _ in range(size):
        x = (x * 1103515245 + 12345) & 0xFFFFFFFF
        out.append(x >> 16 & 0xFF)
    return bytes(out)


def files(specs, seed=0):
    """specs: (fid, name, addr, size, type); yields header + data blocks."""
    blocks = []
    for n, (fid, name, addr, size, ftype) in enumerate(specs):
        blocks.append(header(n, fid, name, addr, size, ftype))
        blocks.append(b'\x04' + payload(seed + n, size))
    return blocks


def fds_side(blocks, tail=b'', allow_over=False):
    s = b''.join(blocks) + tail
    assert allow_over or len(s) <= SIDE, len(s)
    return (s + bytes(SIDE))[:SIDE]


def qd_side(blocks):
    s = b''.join(b + block_crc(b).to_bytes(2, 'little') for b in blocks)
    assert len(s) <= QD_SIDE
    return s + bytes(QD_SIDE - len(s))


def fwnes(sides, count=None):
    return b'FDS\x1a' + bytes([len(sides) if count is None else count]) + bytes(11) + b''.join(sides)


# ---- Mesen transliterations ----------------------------------------------

class Reader:
    def __init__(self, image, data_offset, side, side_bytes, bounded):
        self.image, self.base, self.side_bytes, self.bounded = image, data_offset + side * side_bytes, side_bytes, bounded
        self.data_offset = data_offset

    def inside(self, j):
        off = self.base + j
        return self.data_offset <= off < len(self.image)

    def __call__(self, j):
        if self.bounded and not 0 <= j < self.side_bytes:
            return 0   # Mesen2 read(): 0 outside [0, bufferSize)
        return self.image[self.base + j] if self.inside(j) else 0

    def span(self, j, length):
        """Bytes j..j+length-1, by the same rules as single reads."""
        lo, hi = self.base + j, self.base + j + length
        if self.inside(j) and self.inside(j + length - 1) and (not self.bounded or j + length <= self.side_bytes):
            return bytes(self.image[lo:hi])
        return bytes(self(j + k) for k in range(length))


def mesen099_stream(image, data_offset, side, side_bytes, qd, crc_mode):
    rd = Reader(image, data_offset, side, side_bytes, False)
    out = bytearray(LEAD_IN)                              # :16
    j = 0
    while j < side_bytes:                                  # :18
        t = rd(j)
        if t == 1: length = 56                             # :22
        elif t == 2: length = 2                            # :23
        elif t == 3: length = 16                           # :24
        elif t == 4:                                       # :25
            back = 5 if qd else 3
            length = 1 + rd(j - back) + rd(j - back + 1) * 0x100
        else:
            break                                          # :26
        if qd: length += 2
        if not rd.inside(j + length - 1): break            # defined: 0.9.9 would overread
        block = rd.span(j, length)
        out.append(0x80)                                   # :32
        out += block                                       # :33
        if not qd:
            out += b'\x4d\x62' if crc_mode == 'mesen' else block_crc(block).to_bytes(2, 'little')  # :36-37
        out += bytes(BLOCK_GAP)                            # :40
        j += length                                        # :43
    if len(out) < side_bytes: out += bytes(side_bytes - len(out))   # :129-131
    return bytes(out)


def mesen2_stream(image, data_offset, side, side_bytes, qd, crc_mode):
    rd = Reader(image, data_offset, side, side_bytes, True)
    out = bytearray(LEAD_IN)                               # :29
    j = 0
    while j < side_bytes:                                  # :38
        t = rd(j)
        if t == 1: length = 56
        elif t == 2: length = 2
        elif t == 3: length = 16
        elif t == 4:
            length = 1 + (rd(j - 5) | rd(j - 4) << 8) if qd else 1 + (rd(j - 3) | rd(j - 2) << 8)  # :54-58
        else:
            out.append(0x80)                               # :63
            out += rd.span(j, side_bytes - j)                  # :64
            break
        if qd: length += 2                                 # :70
        if j + length >= side_bytes: break                 # :73
        block = rd.span(j, length)
        out.append(0x80)
        out += block
        if not qd:
            out += b'\x4d\x62' if crc_mode == 'mesen' else block_crc(block).to_bytes(2, 'little')  # :82-83
        out += bytes(BLOCK_GAP)                            # :87
        j += length
    if len(out) < side_bytes: out += bytes(side_bytes - len(out))  # :184-186
    return bytes(out)


# ---- The fixture set -----------------------------------------------------

BASIC = [(0x00, b'KYODAKU-', 0x2800, 0xE0, 2), (0x01, b'CHARDATA', 0x0000, 0x2000, 1),
         (0x0F, b'MAINPRG ', 0x6000, 0x100, 0)]


def fixtures():
    """Yields (filename, bytes, data_offset, side_bytes, sides, qd)."""
    basic = [disk_info(b'BAS'), amount(3)] + files(BASIC)
    side = fds_side(basic)
    yield 'basic.fds', fwnes([side])
    yield 'basic_raw.fds', side
    yield 'basic.qd', qd_side(basic)
    s0 = fds_side([disk_info(b'TWO', side=0), amount(2)] + files(BASIC[:2], 10))
    s1 = fds_side([disk_info(b'TWO', side=1), amount(3)] +
                  files([(0x02, b'SIDEB-1 ', 0x6000, 0x300, 0), (0x03, b'SIDEB-2 ', 0x7000, 0x10, 0),
                         (0x04, b'SIDEB-NT', 0x2000, 0x400, 2)], 20))
    yield 'two_side.fds', fwnes([s0, s1])
    yield 'two_side_raw.fds', s0 + s1
    # Four files on the disk, two in the file amount: the BIOS sees two.
    yield 'hidden.fds', fwnes([fds_side([disk_info(b'HID'), amount(2)] + files(BASIC + [(0x20, b'HIDDEN  ', 0x8000, 0x40, 0)], 30))])
    yield 'missing.fds', fwnes([fds_side([disk_info(b'MIS'), amount(5)] + files(BASIC, 40))])
    # Unclaimed bytes after the last file, in the shape library disks carry.
    residue = b'\x05' + b'\xdb\xb6\x6d' * 5 + b'\xdb\xb6'
    yield 'tail_data.fds', fwnes([fds_side([disk_info(b'TAI'), amount(2)] + files(BASIC[:2], 50), tail=residue)])
    # A stray code before the file amount is reached.
    yield 'cut_short.fds', fwnes([fds_side([disk_info(b'CUT'), amount(3)] + files(BASIC[:2], 55), tail=b'\x07' + payload(99, 64))])
    yield 'no_data.fds', fwnes([fds_side([disk_info(b'NOD'), amount(2), header(0, 0, b'ORPHAN  ', 0x6000, 0x10, 0)])])
    # The last file claims more bytes than the side holds.
    over = [disk_info(b'OVR'), amount(1), header(0, 0, b'TOOLONG ', 0x6000, 0xFFF0, 0)]
    over_side = fds_side(over + [b'\x04' + payload(60, SIDE - 56 - 2 - 16 - 1)])
    yield 'overrun.fds', fwnes([over_side])
    # The same on side 0 of two: 0.9.9 streams on into side 1's bytes.
    yield 'overrun2.fds', fwnes([over_side, s1])
    # A data block that ends exactly at the side's last byte.
    fill = SIDE - 56 - 2 - 16 - 1
    yield 'full.fds', fwnes([fds_side([disk_info(b'FUL'), amount(1), header(0, 0, b'FILLS   ', 0x6000, fill, 0), b'\x04' + payload(70, fill)])])
    trunc_side = fds_side([disk_info(b'TRU'), amount(1)] + files([(0, b'CUTSHORT', 0x6000, 2000, 0)], 80))
    yield 'truncated.fds', fwnes([side, trunc_side])[:16 + SIDE + 1000]
    yield 'trailing.fds', side + bytes(100)
    yield 'no_info.fds', fwnes([side, bytes(SIDE)])
    yield 'bad_sig.fds', fwnes([fds_side([disk_info(b'SIG', signature=b'*NINTENDO-HVX*'), amount(3)] + files(BASIC))])
    bad_qd = bytearray(qd_side(basic))
    bad_qd[56 + 2 + 2 + 2 + 16] ^= 1   # block 1+CRC, block 2+CRC, then the file header: its CRC
    yield 'bad_crc.qd', bytes(bad_qd)
    yield 'zero_sides.fds', fwnes([], count=0)
    yield 'short_raw.fds', side[:SIDE - 1]
    yield 'not_fds.nes', b'NES\x1a' + bytes(12 + 16384)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', type=Path, required=True)
    out = ap.parse_args().out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    streams = 0
    for name, data in fixtures():
        (out / name).write_bytes(data)
        headered = data[:4] == b'FDS\x1a'
        if not headered and data[:15] != b'\x01*NINTENDO-HVC*':
            continue
        qd = name.endswith('.qd')
        side_bytes = QD_SIDE if qd else SIDE
        offset = 16 if headered else 0
        sides = data[4] if headered else len(data) // side_bytes
        for s in range(sides):
            for profile, build in (('mesen099', mesen099_stream), ('mesen2', mesen2_stream)):
                for crc in ('computed', 'mesen'):
                    stream = build(data, offset, s, side_bytes, qd, crc)
                    fname = f'{name}.s{s}.{profile}.{crc}.bin'
                    (out / fname).write_bytes(stream)
                    manifest.append(f'stream {name} {s} {profile} {crc} {fname}')
                    streams += 1
    (out / 'manifest.txt').write_text('\n'.join(manifest) + '\n', newline='\n')
    print(f'{len(list(fixtures()))} images, {streams} expected streams in {out}')


if __name__ == '__main__':
    main()
