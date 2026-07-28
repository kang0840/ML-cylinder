from src.factory_twin import CONVEYOR_FEATURES, CYLINDER_FEATURES, FactoryDigitalTwin


def test_assets_use_different_sensor_schemas_and_models():
    twin = FactoryDigitalTwin(seed=7)
    conveyor = twin.read_conveyor("CV-01")
    cylinder = twin.read_cylinder("CY-01")

    assert set(CONVEYOR_FEATURES).issubset(conveyor)
    assert set(CYLINDER_FEATURES).issubset(cylinder)
    assert "pressure_mpa" not in conveyor
    assert "motor_current_a" not in cylinder
    assert conveyor["ai"]["model"] != cylinder["ai"]["model"]


def test_dummy_sensor_values_stay_in_physical_operating_ranges():
    twin = FactoryDigitalTwin(seed=11)
    conveyor = twin.read_conveyor("CV-02")
    cylinder = twin.read_cylinder("CY-02")

    assert 750 <= conveyor["motor_speed_rpm"] <= 1650
    assert 0.5 <= conveyor["motor_current_a"] <= 8.0
    assert 20 <= conveyor["temperature_c"] <= 105
    assert 0.2 <= cylinder["pressure_mpa"] <= 0.58
    assert 25 <= cylinder["sound_rms_db"] <= 85
    assert cylinder["peak_mm_s"] >= cylinder["vibration_rms_mm_s"]


def test_history_can_be_filtered_by_asset_type():
    twin = FactoryDigitalTwin(seed=13)
    twin.read_conveyor("CV-01")
    twin.read_cylinder("CY-01")

    conveyor_history = twin.history_items("conveyor", 10)
    assert len(conveyor_history) == 1
    assert conveyor_history[0]["equipment_type"] == "conveyor"


def test_factory_api_returns_independent_equipment_data():
    from server import app

    client = app.test_client()
    conveyors = client.get("/api/conveyor").get_json()["conveyors"]
    cylinders = client.get("/api/cylinder").get_json()["cylinders"]
    history = client.get("/api/history?type=cylinder&limit=10").get_json()["history"]

    assert {item["conveyor_id"] for item in conveyors} == {"CV-01", "CV-02"}
    assert {item["cylinder_id"] for item in cylinders} == {"CY-01", "CY-02"}
    assert history and all(item["equipment_type"] == "cylinder" for item in history)

