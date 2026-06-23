from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import os
import resource
import sys
from time import monotonic, process_time


@dataclass(frozen=True)
class ProcessUsage:
    rss_bytes: int | None
    cpu_percent: float | None


class ProcessMonitor:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = monotonic,
        cpu_clock: Callable[[], float] = process_time,
        rss_reader: Callable[[], int | None] | None = None,
    ):
        self.clock = clock
        self.cpu_clock = cpu_clock
        self.rss_reader = rss_reader or current_rss_bytes
        self._last_wall: float | None = None
        self._last_cpu: float | None = None

    def sample(self) -> ProcessUsage:
        wall = self.clock()
        cpu = self.cpu_clock()
        cpu_percent: float | None = None
        if self._last_wall is not None and self._last_cpu is not None:
            wall_delta = wall - self._last_wall
            cpu_delta = cpu - self._last_cpu
            if wall_delta > 0:
                cpu_percent = round(
                    max(0.0, cpu_delta / wall_delta * 100),
                    1,
                )
        self._last_wall = wall
        self._last_cpu = cpu
        return ProcessUsage(
            rss_bytes=self.rss_reader(),
            cpu_percent=cpu_percent,
        )


def current_rss_bytes() -> int | None:
    proc_rss = _linux_proc_rss_bytes()
    if proc_rss is not None:
        return proc_rss

    max_rss = resource.getrusage(
        resource.RUSAGE_SELF
    ).ru_maxrss
    if max_rss <= 0:
        return None
    if sys.platform == "darwin":
        return int(max_rss)
    return int(max_rss * 1024)


def _linux_proc_rss_bytes() -> int | None:
    statm = Path("/proc/self/statm")
    if not statm.exists():
        return None
    try:
        parts = statm.read_text(encoding="utf-8").split()
        rss_pages = int(parts[1])
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (OSError, IndexError, ValueError):
        return None
    return rss_pages * page_size
