import pytest

from src.health_assessment import (
    ZONE_FEATURES, classify_statistical_zone, estimate_rul_from_rms,
    fit_normal_baseline, health_score,
)


def _samples():
    return [{name: value for name in ZONE_FEATURES} for value in (9.0, 10.0, 11.0)]


def test_baseline_and_statistical_zones():
    baseline = fit_normal_baseline(_samples())
    at_mean = {name: 10.0 for name in ZONE_FEATURES}
    assert classify_statistical_zone(at_mean, baseline)["zone"] == "A"
    sigma = baseline[ZONE_FEATURES[0]]["std"]
    at_mean[ZONE_FEATURES[0]] = 10.0 + 2.5 * sigma
    result = classify_statistical_zone(at_mean, baseline)
    assert result["zone"] == "C"
    assert result["controlling_feature"] == ZONE_FEATURES[0]


def test_health_score_mapping():
    assert [health_score(zone) for zone in range(4)] == [100, 75, 50, 25]


def test_rul_uses_positive_rms_trend():
    result = estimate_rul_from_rms([(0.0, 0.8), (10.0, 1.0)], zone_d_rms=1.4)
    assert result["rms_rate_mm_s_per_hour"] == pytest.approx(0.02)
    assert result["hours_to_zone_d"] == pytest.approx(20.0)


def test_rul_is_unknown_without_trend():
    assert estimate_rul_from_rms([(0.0, 0.8)], 1.4)["hours_to_zone_d"] is None
