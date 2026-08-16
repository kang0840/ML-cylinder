from src.session_tracker import SessionTracker

def test_gap_starts_new_session_but_dual_timestamp_stays_together():
    tracker=SessionTracker(gap_seconds=3)
    first=tracker.enrich({"device_id":"pico01","boot_id":"b1","timestamp":100})["session_id"]
    assert tracker.enrich({"device_id":"pico01","boot_id":"b1","timestamp":100})["session_id"]==first
    assert tracker.enrich({"device_id":"pico01","boot_id":"b1","timestamp":104})["session_id"]!=first
