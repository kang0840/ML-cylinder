import pytest
from src.data_validator import validate_sensor_payload

def payload():
    return {"device_id":"pico01","sensor_type":"sph0645","sample_rate":1600,"cylinder_state":"forward","sequence":1,"timestamp":1785830000,"samples":[1,2,3]}

def test_valid_payload():
    assert validate_sensor_payload(payload()).samples == (1.0, 2.0, 3.0)

def test_invalid_sensor():
    value = payload(); value["sensor_type"] = "other"
    with pytest.raises(ValueError): validate_sensor_payload(value)
