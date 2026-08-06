import time

from src.auto_trainer import AutoTrainer
from src.data_validator import SensorPacket
from src.database import Database
from src.feature_extractor import extract_features
from src.ml_predictor import MLPredictor


def test_trains_existing_sensor_regressor_and_swaps_model(tmp_path):
    database = Database(tmp_path / "training.db")
    model_path = tmp_path / "model.pkl"
    predictor = MLPredictor(model_path)
    for sequence in range(2):
        packet = SensorPacket(
            "pico01", "sph0645", 100, "idle", sequence, 1785830000 + sequence,
            (1.0 + sequence, 2.0, 3.0, 4.0), 90.0 - sequence,
        )
        features = extract_features(packet.samples, packet.sample_rate)
        database.save_measurement(packet, features, predictor.predict(features))

    trainer = AutoTrainer(database, predictor, model_path, batch_size=2)
    trainer.start()
    deadline = time.monotonic() + 10
    while not model_path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    trainer.stop()

    assert model_path.exists()
    assert predictor.model_type == "sensor_polynomial_regressor"
    assert database.training_progress()[1] == 0
    database.close()
