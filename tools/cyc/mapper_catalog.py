#!/usr/bin/env python3
"""Generate runner/cyc/MAPPER_CATALOG.md: every known iNES / NES 2.0 mapper ID,
whether the cycle runtime supports it, how many known dumps use it, and a
coarse category, ordered by usefulness.

Inputs (not vendored; pass local copies of a pinned Mesen2 revision):
  --db       Mesen2 UI/Dependencies/MesenNesDB.txt  (NES 2.0 DB + NesCartDB + Nestopia)
  --factory  Mesen2 Core/NES/MapperFactory.cpp       (which IDs exist, board class names)
  --mesen-rev the Mesen2 commit both files came from
  --owner    optional JSON {mapper: count} of locally owned ROMs (counts only)

Dump counts include regional/revision variants and translations: roughly twice
the number of distinct games. They rank priorities; they are not game counts.
"""
import argparse
import collections
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Curated categories for boards whose class name/board string does not say.
LICENSED = {0, 1, 2, 3, 4, 5, 7, 9, 10, 13, 16, 18, 19, 21, 22, 23, 24, 25, 26, 32, 33, 34,
            48, 64, 65, 66, 67, 68, 69, 70, 72, 73, 75, 76, 77, 78, 80, 82, 85, 86, 87, 88,
            89, 92, 93, 94, 95, 96, 97, 101, 118, 119, 140, 152, 153, 154, 155, 157, 158,
            159, 180, 184, 185, 206, 207, 210}
UNLICENSED_COMMERCIAL = {11, 41, 71, 79, 113, 144, 146, 148, 228, 232}  # Tengen/Camerica/Color Dreams/AVE/Caltron/...
HOMEBREW = {28, 29, 30, 31, 111, 218, 682}
SPECIAL = {99: 'Vs. System', 40: 'FDS conversion'}
COPIER = {6, 8, 17, 561, 562}
# Mappers checked on at least one real owner title through a bounded reference
# route (CARTRIDGE_REVIEW.md, nesrecomp-core-playtest campaigns). Keep current.
TITLE_CHECKED = {0, 1, 2, 4, 5, 11, 34, 40, 41, 66, 68, 69, 71, 79, 118, 119, 228}
FAMICLONE = {256, 270}


def category(mapper, cls, boards):
    if mapper in SPECIAL: return SPECIAL[mapper]
    if mapper in LICENSED: return 'licensed'
    if mapper in UNLICENSED_COMMERCIAL: return 'unlicensed commercial'
    if mapper in HOMEBREW: return 'homebrew'
    if mapper in COPIER: return 'copier'
    if mapper in FAMICLONE: return 'famiclone SoC'
    text = (cls or '') + ' ' + ' '.join(boards)
    if re.search(r'Bmc|BMC-|in1|Coolboy|Super40|Fk23', text, re.I): return 'multicart'
    return 'pirate / other'


def supported_ids():
    source = (ROOT / 'runner/cyc/hw_mapper.c').read_text(encoding='utf-8')
    table = source[source.index('} MAPPERS[] = {'):]
    table = table[:table.index('};')]
    return {int(n): name for n, name in re.findall(r'\{\s*(\d+),\s*"([^"]+)"', table)}


def mesen_classes(path):
    """Outer `case N:` labels of CreateMapper's switch and the class each builds.
    Nested submapper switches are indented deeper and are skipped."""
    lines = Path(path).read_text(encoding='utf-8').splitlines()
    start = next(i for i, l in enumerate(lines) if re.match(r'\s*case 0:\s*return new NROM', l))
    indent = len(lines[start]) - len(lines[start].lstrip())
    classes, pending = {}, []
    for line in lines[start:]:
        if 'UnifBoards::' in line: break
        depth = len(line) - len(line.lstrip())
        label = re.match(r'\s*case (\d+):', line)
        if label and depth == indent:
            pending.append(int(label.group(1)))
        built = re.search(r'return new (\w+)', line)
        if built and pending:
            for m in pending: classes[m] = built.group(1)
            pending = []
    return classes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--db', type=Path, required=True)
    ap.add_argument('--factory', type=Path, required=True)
    ap.add_argument('--mesen-rev', required=True)
    ap.add_argument('--owner', type=Path)
    ap.add_argument('--out', type=Path, default=ROOT / 'runner/cyc/MAPPER_CATALOG.md')
    args = ap.parse_args()
    ours = supported_ids()
    classes = mesen_classes(args.factory)
    dumps, boards = collections.Counter(), collections.defaultdict(collections.Counter)
    for line in args.db.read_text(encoding='utf-8', errors='replace').splitlines():
        f = line.split(',')
        if line.startswith('#') or len(f) < 6 or not f[5].isdigit(): continue
        m = int(f[5]); dumps[m] += 1
        if f[2]: boards[m][f[2]] += 1
    owner = {int(k): v for k, v in json.loads(args.owner.read_text()).items()} if args.owner else {}
    ids = sorted(set(classes) | set(ours) | {m for m in dumps if m < 4096 and m != 65000})
    rows = []
    for m in ids:
        board = ', '.join(b for b, _ in boards[m].most_common(2))
        rows.append(dict(id=m, name=ours.get(m) or classes.get(m) or '(no emulator class)',
                         board=board, dumps=dumps[m], owner=owner.get(m, 0), supported=m in ours,
                         category=category(m, classes.get(m), list(boards[m]))))
    total = sum(r['dumps'] for r in rows)
    covered = sum(r['dumps'] for r in rows if r['supported'])
    by_cat = collections.defaultdict(lambda: [0, 0, 0, 0])
    for r in rows:
        c = by_cat[r['category']]; c[0] += 1; c[1] += r['supported']; c[2] += r['dumps']
        c[3] += r['dumps'] if r['supported'] else 0
    order = ['licensed', 'unlicensed commercial', 'homebrew', 'Vs. System', 'FDS conversion',
             'multicart', 'copier', 'famiclone SoC', 'pirate / other']
    out = ['# Mapper catalog', '',
           'Generated by `tools/cyc/mapper_catalog.py`; do not edit by hand. Supported boards and their',
           'behavior, references and limits are in [MAPPERS.md](MAPPERS.md).', '',
           f'Sources: Mesen2 `{args.mesen_rev[:12]}` `MesenNesDB.txt` (NewRisingSun NES 2.0 DB, NesCartDB,',
           'Nestopia) for dump counts and board names; its `MapperFactory.cpp` for which IDs exist.',
           'A dump count includes regional/revision variants and translations, roughly twice the number',
           'of distinct games. Categories are coarse and partly curated.', '',
           '**Validation:** every supported mapper has board-contract tests and generated fixtures run',
           'natively, on both interpreters and on the independent oracle at all four CPU/PPU alignments.',
           'The *Title* column marks mappers also checked on one or two real owner titles (bounded',
           '1500-frame reference route; newer ones also owner playtest). New mappers follow that policy;',
           'it is not exhaustive per-game or physical-hardware validation. Unmarked supported mappers',
           'still need a real title (tracked in beads-2dw.1.38).', '',
           f'**{len(ours)} of {len(rows)} known mapper IDs supported, covering {covered} of {total} known dumps '
           f'({100 * covered / total:.1f}%).**', '',
           '| Category | IDs | Supported | Dumps | Dumps covered |', '|---|---:|---:|---:|---:|']
    for c in order:
        if c in by_cat:
            n, s, d, dc = by_cat[c]
            out.append(f'| {c} | {n} | {s} | {d} | {100 * dc / d if d else 0:.0f}% |')
    def table(title, subset, note=''):
        out.extend(['', f'## {title}', ''] + ([note, ''] if note else []) +
                   ['| ID | Name | Category | Dumps | Owner ROMs | Title | Example board |', '|---:|---|---|---:|---:|:-:|---|'])
        for r in subset:
            mark = 'yes' if r['id'] in TITLE_CHECKED else ('no' if r['supported'] else '')
            out.append(f"| {r['id']} | {r['name']} | {r['category']} | {r['dumps']} | {r['owner'] or ''} | {mark} | {r['board']} |")
    rank = lambda r: (order.index(r['category']), -r['dumps'], r['id'])
    table('Remaining, by priority', sorted((r for r in rows if not r['supported']), key=rank),
          'Ordered by category (licensed first), then dump count. Rows with fewer than about 10 dumps are diminishing returns.')
    table('Supported', sorted((r for r in rows if r['supported']), key=lambda r: r['id']))
    args.out.write_text('\n'.join(out) + '\n', encoding='utf-8', newline='\n')
    print(f'{len(ours)} supported / {len(rows)} IDs; {covered}/{total} dumps; wrote {args.out}')


if __name__ == '__main__':
    main()
