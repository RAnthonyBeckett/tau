import numpy as np
from scipy.signal import lfilter
RATE=16000
def formant_filter(x, f, bw):
    r=np.exp(-np.pi*bw/RATE); th=2*np.pi*f/RATE
    return lfilter([1-r],[1,-2*r*np.cos(th),r*r],x)
def vowel(dur, f0, formants, rng):
    n=int(dur*RATE); t=np.arange(n)/RATE
    pitch=f0*(1+0.05*np.sin(2*np.pi*3*t))+rng.normal(0,0.5,n)
    phase=np.cumsum(pitch/RATE); src=(np.diff(np.floor(phase),prepend=0)>0).astype(float)
    src=lfilter([1],[1,-0.95],src)+0.02*rng.normal(size=n)
    y=sum(formant_filter(src,f,bw) for f,bw in formants)
    env=np.minimum(1,np.minimum(t/0.03,(dur-t)/0.05)); return y*env
V={'a':[(730,90),(1090,110),(2440,170)],'i':[(270,60),(2290,100),(3010,120)],'u':[(300,60),(870,90),(2240,120)],'o':[(570,80),(840,90),(2410,150)],'e':[(530,70),(1840,110),(2480,150)]}
def speech(seconds=1.6, seed=0):
    rng=np.random.default_rng(seed); out=[]; tot=0
    while tot<seconds:
        d=rng.uniform(0.12,0.25); v=rng.choice(list(V)); out.append(vowel(d,rng.uniform(100,140),V[v],rng))
        g=int(rng.uniform(0.03,0.08)*RATE); out.append(rng.normal(0,0.01,g)); tot+=d+g/RATE
    y=np.concatenate(out); y=y/np.max(np.abs(y))*0.5; return (y*32767).astype(np.int16)
