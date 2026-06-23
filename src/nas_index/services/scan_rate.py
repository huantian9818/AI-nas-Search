from collections.abc import Callable
from time import monotonic


class ScanRateTracker:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = monotonic,
    ):
        self.clock = clock
        self._samples: dict[int, tuple[float, int]] = {}

    def sample(
        self,
        *,
        run_id: int,
        processed_entries: int,
    ) -> float | None:
        now = self.clock()
        previous = self._samples.get(run_id)
        self._samples[run_id] = (now, processed_entries)
        if previous is None:
            return None

        previous_time, previous_entries = previous
        elapsed = now - previous_time
        if elapsed <= 0 or processed_entries < previous_entries:
            return None
        return round(
            (processed_entries - previous_entries) / elapsed,
            1,
        )
