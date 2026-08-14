import pytest
from src.data_validator import expand_dual_microphone_payload, validate_sensor_payload

def payload():
    return {"device_id":"pico01","sensor_type":"sph0645","sample_rate":1600,"cylinder_state":"forward","sequence":1,"timestamp":1785830000,"samples":[1,2,3]}

def test_valid_payload():
    assert validate_sensor_payload(payload()).samples == (1.0, 2.0, 3.0)

def test_invalid_sensor():
    value = payload(); value["sensor_type"] = "other"
    with pytest.raises(ValueError): validate_sensor_payload(value)

def test_expands_deployed_dual_microphone_packet():
    packets = expand_dual_microphone_payload({"device_id":"pico01", "sensor_id":"dual_i2s_mic", "sequence":7, "sample_rate_hz":16000, "left_samples":[1,2], "right_samples":[3,4]})
    assert [packet["sensor_type"] for packet in packets] == ["sph0645", "inmp441"]
    assert packets[0]["samples"] == [1, 2]
