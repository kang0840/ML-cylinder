import sqlite3

database = "/opt/smart-cylinder-pi5/data/smart_cylinder.db"
connection = sqlite3.connect(database)
connection.row_factory = sqlite3.Row

print("--- latest model inference ---")
rows = connection.execute(
    """
    SELECT m.measured_at, m.sensor_type, r.prediction, r.confidence,
           r.health_score, r.model_version, r.created_at
    FROM ml_results AS r
    JOIN measurements AS m ON m.measurement_id = r.measurement_id
    ORDER BY r.id DESC
    LIMIT 10
    """
).fetchall()
for row in rows:
    print(dict(row))

print("--- counts by sensor ---")
rows = connection.execute(
    """
    SELECT m.sensor_type, COUNT(*) AS inference_count,
           MAX(r.created_at) AS latest_inference
    FROM ml_results AS r
    JOIN measurements AS m ON m.measurement_id = r.measurement_id
    GROUP BY m.sensor_type
    """
).fetchall()
for row in rows:
    print(dict(row))

connection.close()
