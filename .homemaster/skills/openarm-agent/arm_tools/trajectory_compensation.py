"""Pure, offline trajectory transformation preserving the holding setpoint."""
import numpy as np


def compensate_positions(positions,times,start_bias,end_bias,max_bias=.30):
    p=np.asarray(positions,dtype=float);t=np.asarray(times,dtype=float)
    b0=np.asarray(start_bias,dtype=float);b1=np.asarray(end_bias,dtype=float)
    if p.ndim!=2 or p.shape[1]!=7 or len(p)<2 or t.shape!=(len(p),) or b0.shape!=(7,) or b1.shape!=(7,):
        raise ValueError('invalid trajectory shape')
    if not all(np.all(np.isfinite(x)) for x in (p,t,b0,b1)) or t[0]!=0 or np.any(np.diff(t)<=0):
        raise ValueError('trajectory must be finite and start at time zero with increasing times')
    if np.any(abs(b0)>max_bias) or np.any(abs(b1)>max_bias):raise ValueError('joint compensation exceeds bound')
    s=t/t[-1];h=10*s**3-15*s**4+6*s**5
    return p+b0+h[:,None]*(b1-b0)
