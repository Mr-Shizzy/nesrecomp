#!/usr/bin/env python3
"""The cycle runtime's console output stages (runner/cyc/hw_apu.c audio_emit),
written independently of the C for the tests and the nesref A/B gates.

Each stage is a first-order RC section discretized at the output rate the way
hw_apu.c does it: a = RC / (RC + dt) for a high-pass
(y[n] = a * (y[n-1] + x[n] - x[n-1])), a = dt / (RC + dt) for a low-pass
(y[n] = y[n-1] + a * (x[n] - y[n-1])).

  nes      high-pass 90 Hz, high-pass 440 Hz, low-pass 14 kHz (nesdev APU Mixer,
           blargg's front-loader measurements)
  famicom  high-pass 37 Hz (nesdev APU Mixer: the only stage the Famicom's
           audio path specifies before its RF modulator)

  python console_output.py            prints each model's corners and response
"""
import cmath
import math

MODELS = {
    'nes': (('hp', 90.0), ('hp', 440.0), ('lp', 14000.0)),
    'famicom': (('hp', 37.0),),
}


def coefficients(model, rate):
    dt = 1.0 / rate
    out = []
    for kind, fc in MODELS[model]:
        rc = 1.0 / (2 * math.pi * fc)
        out.append((kind, rc / (rc + dt) if kind == 'hp' else dt / (rc + dt)))
    return out


def stage(x, rate, model):
    """Run a float sequence (any iterable; numpy arrays come back as numpy)
    through a model's output stage."""
    stages = coefficients(model, rate)
    state = [[0.0, 0.0] for _ in stages]           # hp: (prev_in, prev_out); lp: (out, -)
    values = x.tolist() if hasattr(x, 'tolist') else list(x)
    y = []
    for v in values:
        for (kind, a), st in zip(stages, state):
            if kind == 'hp':
                o = a * (st[1] + v - st[0])
                st[0], st[1] = v, o
            else:
                st[0] += a * (v - st[0])
                o = st[0]
            v = o
        y.append(v)
    if hasattr(x, 'tolist'):
        import numpy as np
        return np.asarray(y, dtype=float)
    return y


def response(model, rate, f):
    """|H| of the discretized stage at frequency f (the difference equations'
    transfer function, H(z) = a (1 - z^-1) / (1 - a z^-1) and a / (1 - (1-a) z^-1))."""
    z1 = cmath.exp(-2j * math.pi * f / rate)
    h = 1.0
    for kind, a in coefficients(model, rate):
        h *= a * (1 - z1) / (1 - a * z1) if kind == 'hp' else a / (1 - (1 - a) * z1)
    return abs(h)


def analog_response(model, f):
    """|H| of the analog RC prototypes the model names."""
    h = 1.0
    for kind, fc in MODELS[model]:
        r = f / fc
        h *= r / math.hypot(1, r) if kind == 'hp' else 1 / math.hypot(1, r)
    return h


def corner(model, rate, kind, lo, hi):
    """The frequency in [lo, hi] where the discretized model is 3.01 dB below
    its passband maximum (bisection on a monotonic edge)."""
    peak = max(response(model, rate, f) for f in (1000.0, 2000.0, 3000.0, 5000.0))
    target = peak / math.sqrt(2)
    for _ in range(80):
        mid = math.sqrt(lo * hi)
        below = response(model, rate, mid) < target
        if (kind == 'hp') == below:
            lo = mid
        else:
            hi = mid
    return math.sqrt(lo * hi)


def main():
    rate = 48000
    for model, stages in MODELS.items():
        print(f'{model}: ' + ', '.join(f'{k} {fc:g} Hz' for k, fc in stages))
        for f in (20, 37, 90, 440, 1000, 5000, 10000, 14000, 20000):
            print(f'  {f:6d} Hz  discrete {20 * math.log10(response(model, rate, f)):7.2f} dB   '
                  f'analog {20 * math.log10(analog_response(model, f)):7.2f} dB')


if __name__ == '__main__':
    main()
