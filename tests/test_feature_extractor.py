import numpy as np
from src.feature_extractor import extract_features

def test_time_features():
    result = extract_features(np.array([-2., 0., 2., 0.]), 100)
    assert result["peak"] == 2
    assert result["peak_to_peak"] == 4
    assert result["rms"] == np.sqrt(2)
