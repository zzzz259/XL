"""Short independent lease transactions; never touches the owner's ResourceSlot."""

import math
import threading


class LeaseRenewer:
    def __init__(self, renew, *, interval):
        if (
            type(interval) not in (int, float)
            or not math.isfinite(interval)
            or interval <= 0
        ):
            raise ValueError("finite renewal interval required")
        self.renew = renew
        self.interval = interval
        self.lost = threading.Event()
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="portrait-lease-renew")
        self._started = False

    def start(self):
        self.thread.start()
        self._started = True

    def _once(self):
        try:
            owned = self.renew() is True
        except Exception:  # noqa: BLE001 - every uncertain renewal loses authority
            owned = False
        if not owned:
            self.lost.set()
        return owned

    def _run(self):
        while not self._stop.wait(self.interval):
            if not self._once():
                return

    def stop(self):
        self._stop.set()
        if self._started:
            self.thread.join()

    def final_fence(self):
        # Join any in-flight transaction before the final wall-clock renew.
        # No helper transaction can race after the subsequent fenced finish.
        self.stop()
        return not self.lost.is_set() and self._once()


class Cancellation:
    def __init__(self, *events):
        self.events = tuple(event for event in events if event is not None)

    def is_set(self):
        return any(event.is_set() for event in self.events)
