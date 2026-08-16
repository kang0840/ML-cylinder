import pandas as pd
from src.data_quality import assess_frame,training_safe

def test_fallback_prediction_is_not_ground_truth():
    frame=pd.DataFrame([{"timestamp":"2026-01-01T00:00:00Z","sensor_type":"sph0645","cylinder_state":"idle","ground_truth":None,"rms":1.0,"dominant_frequency":100,"dominant_amplitude":2,"prediction":"normal","model_version":"fallback-vibration-threshold-v1"}])
    report=assess_frame(frame)
    assert report.class_counts["normal"]==0
    assert report.unsafe_training_rows==1
    assert training_safe(frame).empty

def test_only_verified_experiments_are_training_safe():
    rows=[{"ground_truth":"normal","ground_truth_source":"controlled_experiment","experiment_id":"normal_01","session_id":"s1"},{"ground_truth":"seal_leak","ground_truth_source":"threshold","experiment_id":"leak_01","session_id":"s2"}]
    assert len(training_safe(pd.DataFrame(rows)))==1
