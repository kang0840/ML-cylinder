import sqlite3
import pytest
from src.data_validator import SensorPacket
from src.database import Database, DuplicateMeasurementError
from src.feature_extractor import extract_features
from src.ml_predictor import MLPredictor

def test_save_is_atomic_and_duplicate_safe(tmp_path):
    db = Database(tmp_path/"test.db")
    packet = SensorPacket("pico01","sph0645",100,"idle",1,1785830000,(1.,2.,3.,4.))
    features = extract_features(packet.samples, packet.sample_rate)
    result = MLPredictor(tmp_path/"none.pkl").predict(features)
    db.save_measurement(packet, features, result)
    with pytest.raises(DuplicateMeasurementError): db.save_measurement(packet, features, result)
    with sqlite3.connect(tmp_path/"test.db") as con:
        assert con.execute("select count(*) from measurements").fetchone()[0] == 1
        assert con.execute("select count(*) from raw_sensor_data").fetchone()[0] == 4
    db.close()
