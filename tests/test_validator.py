import pytest
from src.data_validator import expand_dual_microphone_payload, validate_sensor_payload

def payload():
    return {"device_id":"pico01","sensor_type":"sph0645","sample_rate":1600,"cylinder_state":"forward","sequence":1,"timestamp":1785830000,"samples":[1,2,3]}

def test_valid_payload():
    packet=validate_sensor_payload(payload())
    assert packet.samples == (1.0, 2.0, 3.0)
    assert packet.cylinder_state == "extending"

def test_ground_truth_requires_experiment_session_and_trusted_source():
    value=payload();value.update({"ground_truth":"seal_leak"})
    with pytest.raises(ValueError):validate_sensor_payload(value)
    value.update({"experiment_id":"seal_leak_01","session_id":"session_01","ground_truth_source":"controlled_experiment","pressure_mpa":0.5,"load_kg":0})
    packet=validate_sensor_payload(value)
    assert packet.ground_truth=="seal_leak"
    assert packet.pressure_mpa==0.5

def test_invalid_sensor():
    value = payload(); value["sensor_type"] = "other"
    with pytest.raises(ValueError): validate_sensor_payload(value)

def test_expands_deployed_dual_microphone_packet():
    packets = expand_dual_microphone_payload({"device_id":"pico01", "sensor_id":"dual_i2s_mic", "sequence":7, "sample_rate_hz":16000, "left_samples":[1,2], "right_samples":[3,4]})
    assert [packet["sensor_type"] for packet in packets] == ["sph0645", "inmp441"]
    assert packets[0]["samples"] == [1, 2]
