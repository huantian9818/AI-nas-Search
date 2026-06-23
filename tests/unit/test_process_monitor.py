from nas_index.services.process_monitor import ProcessMonitor


def test_process_monitor_reports_memory_and_cpu_delta():
    wall_times = iter([100.0, 102.0])
    cpu_times = iter([10.0, 10.5])
    monitor = ProcessMonitor(
        clock=lambda: next(wall_times),
        cpu_clock=lambda: next(cpu_times),
        rss_reader=lambda: 64 * 1024 * 1024,
    )

    first = monitor.sample()
    second = monitor.sample()

    assert first.rss_bytes == 64 * 1024 * 1024
    assert first.cpu_percent is None
    assert second.rss_bytes == 64 * 1024 * 1024
    assert second.cpu_percent == 25.0
