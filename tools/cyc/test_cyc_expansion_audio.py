#!/usr/bin/env python3
"""Measure rendered expansion PCM from the execution-harness tone fixtures.

First build fixtures with test_cyc_runtime.py --case-prefix exp_tone_. Pass its
--out directory as --fixtures here. Requires no ROMs or third-party Python libs.
"""
import argparse, json, math, struct, subprocess, wave
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--fixtures',type=Path,help='VRC6 tone fixture directory')
    ap.add_argument('--fm-fixtures',type=Path,help='VRC7 fixture directory')
    ap.add_argument('--mmc5-fixtures',type=Path,help='MMC5 fixture directory')
    ap.add_argument('--s5b-fixtures',type=Path,help='Sunsoft 5B fixture directory (s5b_ prefix)')
    ap.add_argument('--n163-fixtures',type=Path,help='Namco 163 fixture directory (n163_ prefix)')
    ap.add_argument('--console',choices=('nes','famicom'),help='output stage (default: the board default: famicom for VRC6, VRC7 and N163)')
    args=ap.parse_args();measurements=[];cases=[]
    if args.fixtures:
        for mapper in (24,26):
            for voice,divisor in (('pulse',16*254),('saw',14*128)):
                cases.append((args.fixtures.resolve(),f'exp_tone_{mapper}_{voice}',(21477272.7272727/12)/divisor))
    if args.fm_fixtures:
        root=args.fm_fixtures.resolve()
        cases += [(root,'fm_nes2_tone_85_2',(3579545/72)*290/32768),
                  (root,'fm_nes2_tone_85_1',None),(root,'fm_nes2_tone_85_2_reset',None)]
    if args.mmc5_fixtures:
        cases += [(args.mmc5_fixtures.resolve(),f'mmc5_tone{i}',(21477272.7272727/12)/(16*254)) for i in range(2)]
    if args.s5b_fixtures:
        from sunsoft_fixtures import S5B_TONES
        cases += [(args.s5b_fixtures.resolve(),name,'noise' if hz is None else hz) for name,hz in S5B_TONES.items()]
    if args.n163_fixtures:
        from namco_fixtures import N163_TONES
        cases += [(args.n163_fixtures.resolve(),name,hz) for name,hz in N163_TONES.items()]
    if not cases: ap.error('pass --fixtures, --fm-fixtures, --mmc5-fixtures, --s5b-fixtures or --n163-fixtures')
    for root,name,expected in cases:
            case=root/name
            suffix='.exe' if hasattr(subprocess,'CREATE_NO_WINDOW') else ''
            exe=root/'build'/'Release'/(name+suffix)
            if not exe.exists():exe=root/'build'/(name+suffix)
            for align in range(4):
                rendered=[]
                for mode,extra in (('native',[]),('interp',['--interp-only'])):
                    wav=case/f'pcm_a{align}_{mode}.wav'
                    p=subprocess.run([str(exe),str(case/(name+'.nes')),'--frames','90','--align',str(align),'--wav-out',str(wav)]+extra+(['--console',args.console] if args.console else []),
                        capture_output=True,text=True,timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    (wav.with_suffix('.log')).write_text(p.stdout+p.stderr)
                    assert p.returncode==0,(name,mode,p.stderr)
                    with wave.open(str(wav)) as f:
                        rate=f.getframerate();data=f.readframes(f.getnframes())
                        assert f.getnchannels()==1 and f.getsampwidth()==2
                    rendered.append(data)
                assert rendered[0]==rendered[1],(name,align,'native/interpreter PCM differs')
                samples=struct.unpack('<'+'h'*(len(data)//2),data)[rate//2:]
                assert len(samples)>rate//2
                center=sum(samples)/len(samples)
                rms=math.sqrt(sum((x-center)**2 for x in samples)/len(samples))
                if expected is None:
                    assert rms<1,(name,align,'expected silence',rms)
                    measurements.append(dict(case=name,align=align,rms=rms,expected='silence'))
                    continue
                if expected=='noise':
                    # Aperiodic: audible, unclipped, and many level changes.
                    changes=sum(1 for i in range(1,len(samples)) if samples[i]!=samples[i-1])
                    assert rms>50 and changes>1000 and max(samples)<32767 and min(samples)>-32768,(name,rms,changes)
                    measurements.append(dict(case=name,align=align,rms=rms,expected='noise',changes=changes))
                    continue
                crossings=[i for i in range(1,len(samples)) if samples[i-1]<=center<samples[i]]
                assert len(crossings)>100
                frequency=rate*(len(crossings)-1)/(crossings[-1]-crossings[0])
                assert abs(frequency-expected)<1,(name,align,frequency,expected)
                assert rms>50 and max(samples)<32767 and min(samples)>-32768,(name,rms)
                measurements.append(dict(case=name,align=align,frequency=frequency,expected=expected,rms=rms))
    for root in {case[0] for case in cases}:
        (root/'pcm-measurements.json').write_text(json.dumps(measurements,indent=2))
    print(f'{len(measurements)} PCM measurements passed; native/interpreter WAVs identical')

if __name__=='__main__':main()
