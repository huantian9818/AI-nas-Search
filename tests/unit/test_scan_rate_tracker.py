from nas_index.services.scan_rate import ScanRateTracker


def test_scan_rate_tracker_reports_recent_entry_rate():
    times = iter([10.0, 12.0])
    tracker = ScanRateTracker(clock=lambda: next(times))

    first = tracker.sample(run_id=1, processed_entries=100)
    second = tracker.sample(run_id=1, processed_entries=340)

    assert first is None
    assert second == 120.0


def test_scan_rate_tracker_resets_for_new_run():
    times = iter([10.0, 12.0])
    tracker = ScanRateTracker(clock=lambda: next(times))

    tracker.sample(run_id=1, processed_entries=100)
    rate = tracker.sample(run_id=2, processed_entries=340)

    assert rate is None
