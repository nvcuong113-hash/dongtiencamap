import math
from quants.learning import _fit_logistic, _predict, _brier

def test_logistic_calibration_outputs_probability():
    x=[20,25,30,35,40,45,50,55,60,65,70,75,80,85]*4
    y=[0,0,0,0,0,0,1,0,1,1,1,1,1,1]*4
    model=_fit_logistic(x,y,steps=1200)
    probs=[_predict(model,v) for v in (20,50,80)]
    assert all(0 < p < 1 for p in probs)
    assert probs[0] < probs[1] < probs[2]

def test_brier_is_mean_squared_probability_error():
    assert abs(_brier([.1,.9],[0,1])-.01)<1e-9
