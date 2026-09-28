#!/usr/bin/env python3
"""A cycle-stepped model of the FDS sound unit, written from the references
independently of runner/cyc/hw_fds_audio.c, and a replay that checks a cyc
run against it.

Profiles (hw_fds_audio.c has the table of differences):
  mesen     libretro/Mesen master 0102910 Core/FdsAudio.h, ModChannel.h,
            BaseFdsChannel.h: what nesref's core runs (the default)
  mesen2    SourMesen/Mesen2 b9fa69d Core/NES/Mappers/FDS/FdsAudio.cpp
  hardware  mesen2's synthesis (only the output stage differs, which the
            CPU cannot see)

State layout: Mesen's StreamState order and widths, as cyc_fds_audio_state()
writes it (CYC_FDS_AUDIO_STATE_BYTES = 171) and as a nesref savestate holds
it (fds_audio_gates.py parses those).

replay(): a cyc run's always-on ring (--ring-out) holds every FDS register
write and every driven read with its CPU cycle; its --frame-log holds the
sound unit's state at each frame's snapshot. Stepping the model one clock per
CPU cycle between those events (the access of cycle N comes after that
cycle's clock; a snapshot at cycle count S follows S clocks) predicts every
$4040-$4092 read and every snapshot, which must match exactly.
"""
import struct

FIELDS = [
    ('vol_speed', 1), ('vol_gain', 1), ('vol_off', 1), ('vol_inc', 1), ('vol_freq', 2), ('vol_timer', 4),
    ('vol_master', 1),
    ('mod_speed', 1), ('mod_gain', 1), ('mod_off', 1), ('mod_inc', 1), ('mod_freq', 2), ('mod_timer', 4),
    ('mod_master', 1),
    ('mod_counter', 1), ('mod_disabled', 1), ('mod_pos', 1), ('mod_overflow', 2), ('mod_table', 64),
    ('mod_output', 4),
    ('wave_write', 1), ('env_disabled', 1), ('halt', 1), ('master_vol', 1), ('wave_overflow', 2),
    ('wave_pitch', 4), ('wave_pos', 1), ('out_level', 1), ('wave', 64),
]
STATE_BYTES = sum(w for _, w in FIELDS)
SIGNED = {'mod_counter', 'mod_output', 'wave_pitch'}
assert STATE_BYTES == 171

MOD_STEP = (0, 1, 2, 4, None, -4, -2, -1)        # ModChannel.h:10, None = reset to 0
MASTER_VOLUME = (36, 24, 17, 14)                  # FdsAudio.h:16
U32 = 0xFFFFFFFF


def decode(blob):
    """Canonical state bytes -> {field: int, or bytes for the tables}."""
    out, o = {}, 0
    for name, w in FIELDS:
        raw = blob[o:o + w]
        o += w
        out[name] = bytes(raw) if w == 64 else int.from_bytes(raw, 'little', signed=name in SIGNED)
    return out


def encode(s):
    b = bytearray()
    for name, w in FIELDS:
        v = s[name]
        b += bytes(v) if w == 64 else (v & ((1 << 8 * w) - 1)).to_bytes(w, 'little')
    return bytes(b)


class Channel:
    """BaseFdsChannel: $4080-$4083 (volume) or $4084-$4087 (modulator)."""

    def __init__(self):
        self.speed = self.gain = self.off = self.inc = self.freq = self.timer = 0
        self.master = 0xE8        # BaseFdsChannel.h:17 (0102910; the 0.9.9 release has $FF)
        self.loaded = False       # a write has loaded the timer (else it runs down from 0 at power-on)
        self.changes = 0          # gain changes by the envelope (the ring's fds.env events count these)

    def reset_timer(self):
        self.timer = (8 * (self.speed + 1) * self.master) & U32
        self.loaded = True

    def write(self, reg, value):
        if reg == 0:
            self.speed, self.inc, self.off = value & 0x3F, (value >> 6) & 1, value >> 7
            self.reset_timer()
            if self.off:
                self.gain = self.speed
        elif reg == 2:
            self.freq = (self.freq & 0xF00) | value
        elif reg == 3:
            self.freq = (self.freq & 0xFF) | (value & 0x0F) << 8

    def tick_envelope(self):
        if self.off or self.master == 0:
            return False
        self.timer = (self.timer - 1) & U32
        if self.timer:
            return False
        self.reset_timer()
        if self.inc and self.gain < 32:
            self.gain += 1
            self.changes += 1
        elif not self.inc and self.gain > 0:
            self.gain -= 1
            self.changes += 1
        return True


class FdsAudio:
    def __init__(self, profile='mesen'):
        assert profile in ('mesen', 'mesen2', 'hardware'), profile
        self.m2 = profile != 'mesen'
        self.vol, self.mod = Channel(), Channel()
        self.counter = self.mod_disabled = self.mod_pos = self.mod_overflow = self.mod_output = 0
        self.mod_table = [0] * 64
        self.wave = [0] * 64
        self.wave_write = self.env_disabled = self.halt = self.master_vol = 0
        self.wave_overflow = self.wave_pos = self.out_level = 0
        self.last_pitch = 0       # the pitch the wave last advanced by (diagnostics),
        self.recent_pitches = []  # and the last four different ones
        self.wave_steps = self.mod_steps = 0   # since power-on (the ring's fds.audio events count these)
        self.enabled = True       # $4023.1 (the RAM Adapter, FDS.cpp:352)
        # Entries a program has written: Mesen leaves both tables as
        # uninitialized heap until then, so only these compare.
        self.wave_defined = [False] * 64
        self.mod_defined = [False] * 64

    # ModChannel.h
    def set_counter(self, v):
        v = (v + 128) % 256 - 128                 # the int8_t parameter
        if v >= 64:
            v -= 128
        elif v < -64:
            v += 128
        self.counter = v

    def update_mod_output(self, pitch):
        temp = self.counter * self.mod.gain
        remainder = temp & 0xF
        temp >>= 4                                # Python >> floors, like the arithmetic shift
        if remainder > 0 and (temp & 0x80) == 0:
            temp += -1 if self.counter < 0 else 2
        if temp >= 192:
            temp -= 256
        elif temp < -64:
            temp += 256
        temp = pitch * temp
        remainder = temp & 0x3F
        temp >>= 6
        if remainder >= 32:
            temp += 1
        self.mod_output = temp

    def tick_modulator(self):
        if self.mod_disabled or self.mod.freq == 0:
            return False
        self.mod_overflow = (self.mod_overflow + self.mod.freq) & 0xFFFF
        if self.mod_overflow >= self.mod.freq:
            return False
        step = MOD_STEP[self.mod_table[self.mod_pos]]
        self.set_counter(0 if step is None else self.counter + step)
        self.mod_pos = (self.mod_pos + 1) & 0x3F
        self.mod_steps += 1
        return True

    # FdsAudio.h
    def update_output(self):
        if self.m2 and self.wave_write:
            return
        level = min(self.vol.gain, 32) * MASTER_VOLUME[self.master_vol]
        self.out_level = (self.wave[self.wave_pos] * level // 1152) & 0xFF

    def advance(self, pitch):
        if pitch != self.last_pitch:
            self.recent_pitches = ([pitch] + [q for q in self.recent_pitches if q != pitch])[:4]
        self.last_pitch = pitch
        self.wave_overflow = (self.wave_overflow + pitch) & 0xFFFF
        if self.wave_overflow < pitch:
            self.wave_pos = (self.wave_pos + 1) & 0x3F
            self.wave_steps += 1

    def clock(self):
        freq = self.vol.freq
        if not self.halt and not self.env_disabled:
            self.vol.tick_envelope()
            if self.mod.tick_envelope():
                self.update_mod_output(freq)
        if self.tick_modulator():
            self.update_mod_output(freq)
        pitch = freq + self.mod_output
        if self.m2:
            self.update_output()
            if not self.halt and pitch > 0:
                self.advance(pitch)
        elif self.halt:
            self.wave_pos = 0
            self.update_output()
        else:
            self.update_output()
            if pitch > 0 and not self.wave_write:
                self.advance(pitch)

    def write(self, addr, value):
        if addr == 0x4023:
            self.enabled = (value >> 1) & 1
            return
        if not self.enabled or addr < 0x4040:
            return
        if addr <= 0x407F:
            if self.wave_write:
                self.wave[addr & 0x3F] = value & 0x3F
                self.wave_defined[addr & 0x3F] = True
        elif addr in (0x4080, 0x4082):
            self.vol.write(addr & 3, value)
            if self.m2:
                self.update_mod_output(self.vol.freq)
        elif addr == 0x4083:
            self.env_disabled, self.halt = (value >> 6) & 1, value >> 7
            if self.m2 and self.halt:
                self.wave_pos = 0
            if self.env_disabled:
                self.vol.reset_timer()
                self.mod.reset_timer()
            self.vol.write(3, value)
            if self.m2:
                self.update_mod_output(self.vol.freq)
        elif addr == 0x4084:
            self.mod.write(0, value)
            self.update_mod_output(self.vol.freq)
        elif addr == 0x4085:
            self.set_counter(value & 0x7F)
            self.update_mod_output(self.vol.freq)
        elif addr == 0x4086:
            self.mod.write(2, value)
        elif addr == 0x4087:
            self.mod.write(3, value)
            self.mod_disabled = value >> 7
            if self.mod_disabled:
                self.mod_overflow = 0
        elif addr == 0x4088:
            if self.mod_disabled:
                self.mod_table[self.mod_pos] = value & 7
                self.mod_table[(self.mod_pos + 1) & 0x3F] = value & 7
                self.mod_defined[self.mod_pos] = self.mod_defined[(self.mod_pos + 1) & 0x3F] = True
                self.mod_pos = (self.mod_pos + 2) & 0x3F
        elif addr == 0x4089:
            self.master_vol, self.wave_write = value & 3, value >> 7
        elif addr == 0x408A:
            self.vol.master = self.mod.master = value

    def read(self, addr):
        """The 6 bits the sound unit drives, or None (open bus)."""
        if not self.enabled or addr < 0x4040:
            return None
        if addr <= 0x407F:
            return self.wave[addr & 0x3F] if self.wave_write else self.wave[self.wave_pos]
        if addr == 0x4090:
            return self.vol.gain
        if addr == 0x4092:
            return self.mod.gain
        return None

    def state(self):
        v, m = self.vol, self.mod
        return encode(dict(
            vol_speed=v.speed, vol_gain=v.gain, vol_off=v.off, vol_inc=v.inc, vol_freq=v.freq, vol_timer=v.timer,
            vol_master=v.master, mod_speed=m.speed, mod_gain=m.gain, mod_off=m.off, mod_inc=m.inc, mod_freq=m.freq,
            mod_timer=m.timer, mod_master=m.master, mod_counter=self.counter, mod_disabled=self.mod_disabled,
            mod_pos=self.mod_pos, mod_overflow=self.mod_overflow, mod_table=bytes(self.mod_table),
            mod_output=self.mod_output, wave_write=self.wave_write, env_disabled=self.env_disabled, halt=self.halt,
            master_vol=self.master_vol, wave_overflow=self.wave_overflow, wave_pitch=0, wave_pos=self.wave_pos,
            out_level=self.out_level, wave=bytes(self.wave)))

    def load(self, blob):
        s = decode(blob)
        v, m = self.vol, self.mod
        v.speed, v.gain, v.off, v.inc, v.freq, v.timer, v.master = (
            s['vol_speed'], s['vol_gain'], s['vol_off'], s['vol_inc'], s['vol_freq'], s['vol_timer'], s['vol_master'])
        m.speed, m.gain, m.off, m.inc, m.freq, m.timer, m.master = (
            s['mod_speed'], s['mod_gain'], s['mod_off'], s['mod_inc'], s['mod_freq'], s['mod_timer'], s['mod_master'])
        self.counter, self.mod_disabled, self.mod_pos = s['mod_counter'], s['mod_disabled'], s['mod_pos']
        self.mod_overflow, self.mod_table, self.mod_output = s['mod_overflow'], list(s['mod_table']), s['mod_output']
        self.wave_write, self.env_disabled, self.halt = s['wave_write'], s['env_disabled'], s['halt']
        self.master_vol, self.wave_overflow, self.wave_pos = s['master_vol'], s['wave_overflow'], s['wave_pos']
        self.out_level, self.wave = s['out_level'], list(s['wave'])


def diff_fields(a, b, skip=()):
    """Names of the fields that differ between two state blobs."""
    da, db = decode(a), decode(b)
    return [n for n, _ in FIELDS if n not in skip and da[n] != db[n]]


# ------------------------------------------------------------------ replay

def ring_sound_events(ring_path):
    """(events, first): events = (cycle, 'w'|'r', addr, value) for $4023
    writes and $4040-$4092 accesses in a --ring-out dump, in ring order;
    first = None if the ring still holds every event since power-on, else
    the cycle of the oldest event it holds (older ones were evicted)."""
    out, first, complete = [], None, True
    with open(ring_path) as f:
        for line in f:
            if line.startswith('#'):
                if 'oldest index' in line:
                    complete = line.split('oldest index ')[1].split(')')[0] == '0'
                continue
            p = line.split()
            if first is None and len(p) > 2:
                first = int(p[2])
            if len(p) < 6 or p[3] not in ('fds.write', 'fds.read'):
                continue
            addr, value = int(p[4], 16), int(p[5], 16)
            if addr == 0x4023 or 0x4040 <= addr <= 0x4092:
                out.append((int(p[2]), 'w' if p[3] == 'fds.write' else 'r', addr, value))
    return out, (None if complete else first)


def replay_run(ring_path, frame_log_path, profile='mesen', on_snapshot=None):
    """replay() over a cyc run's ring and frame log: from power-on if the ring
    holds everything, else from the first snapshot after its oldest event."""
    events, first = ring_sound_events(ring_path)
    snaps = frame_log_states(frame_log_path)
    start = None
    if first is not None:
        start = min(c for c in snaps if c > first)
    return replay(events, snaps, profile, start, on_snapshot) + (start,)


def step(blob, clocks, profile='mesen'):
    """The state after `clocks` more cycles with no register access."""
    m = FdsAudio(profile)
    m.load(blob)
    for _ in range(clocks):
        m.clock()
    return m.state()


def replay(events, snapshots, profile='mesen', start=None, on_snapshot=None):
    """Step the model through a run. snapshots: {cycle count: state bytes}.
    start: None = from power-on (the ring must hold every event since), or
    the cycle count of a snapshot to start from (its state is loaded; events
    of that cycle and later are replayed; $4023 is taken to enable the sound
    registers). Returns (reads checked, snapshots checked, [mismatch
    descriptions]). on_snapshot(cycle, model) is called at each snapshot."""
    model = FdsAudio(profile)
    clocks = 0
    if start is not None:
        model.load(snapshots[start])
        model.enabled = True
        clocks = start
    bad, reads, snaps = [], 0, 0
    marks = sorted([(c, 1, i, None) for i, c in enumerate(snapshots) if c > clocks] +
                   [(e[0], 0, i, e) for i, e in enumerate(events) if e[0] >= clocks])
    for cycle, kind, _, e in marks:
        if kind == 1:
            while clocks < cycle:                 # a snapshot at count S follows S clocks
                model.clock()
                clocks += 1
            snaps += 1
            if on_snapshot:
                on_snapshot(cycle, model)
            got = snapshots[cycle]
            want = model.state()
            if got != want:
                bad.append(f'snapshot at cycle {cycle}: ' + ', '.join(
                    f'{n} cyc={decode(got)[n]!r} model={decode(want)[n]!r}' for n in diff_fields(got, want)))
            continue
        while clocks < cycle + 1:                 # the access of cycle N follows N + 1 clocks
            model.clock()
            clocks += 1
        _, rw, addr, value = e
        if rw == 'w':
            model.write(addr, value)
        else:
            want = model.read(addr)
            reads += 1
            if want is None or (value & 0x3F) != want:
                bad.append(f'read ${addr:04X} at cycle {cycle}: cyc {value & 0x3F:02X} model {want}')
    return reads, snaps, bad


def frame_log_states(path):
    """{cycle count: sound state} from a cyc --frame-log (version 2)."""
    data = open(path, 'rb').read()
    assert data[:8] == b'CYCFRAME' and struct.unpack_from('<I', data, 8)[0] == 2
    pos, out = 12, {}
    while pos < len(data):
        head = struct.unpack_from('<9I', data, pos)
        pos += 36 + 288 + sum(head[3:8])
        out[head[1] | head[2] << 32] = data[pos:pos + head[8]]
        pos += head[8]
    return out
