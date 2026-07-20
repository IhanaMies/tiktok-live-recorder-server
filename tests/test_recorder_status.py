import multiprocessing
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.tiktok_recorder import TikTokRecorder  # noqa: E402
from utils.custom_exceptions import UserLiveError  # noqa: E402
from utils.enums import Mode  # noqa: E402
from utils.recorder_config import RecorderConfig  # noqa: E402
from utils.user_store import UserState  # noqa: E402


class FakeTikTokAPI:
    """Returns "123" on the first call (to satisfy _setup), then follows `behaviors`."""

    def __init__(self, behaviors):
        self._setup_done = False
        self._behaviors = list(behaviors)

    def is_country_blacklisted(self):
        return False

    def get_room_id_from_user(self, user):
        if not self._setup_done:
            self._setup_done = True
            return "123"
        if not self._behaviors:
            raise UserLiveError("no more behaviors")
        b = self._behaviors.pop(0)
        if b == "offline":
            raise UserLiveError("not live")
        if b == "live":
            return "123"
        if b == "boom":
            raise RuntimeError("boom")
        raise AssertionError(f"unknown behavior {b!r}")

    def is_room_alive(self, room_id):
        return True


def _make_recorder(behaviors, status_dict, start_recording_fn):
    config = RecorderConfig(
        mode=Mode.AUTOMATIC,
        user="alice",
        cookies={},
        automatic_interval=1,
        status_dict=status_dict,
        username="alice",
    )
    rec = TikTokRecorder(config)
    rec.tiktok = FakeTikTokAPI(behaviors)
    rec._setup()
    rec.start_recording = start_recording_fn  # type: ignore[assignment]
    return rec


def _set_removed(status_dict, user="alice"):
    entry = status_dict.get(user, {})
    entry["removed"] = True
    status_dict[user] = entry


def test_live_then_removed_publishes_stopped():
    manager = multiprocessing.Manager()
    status = manager.dict()
    try:
        entered_recording = threading.Event()

        def fake_start_recording(user, room_id):
            entered_recording.set()

        rec = _make_recorder(["live"], status, fake_start_recording)

        def tripper():
            entered_recording.wait(timeout=5)
            _set_removed(status)

        t = threading.Thread(target=tripper, daemon=True)
        t.start()
        rec.automatic_mode()
        t.join(timeout=2)

        final = status["alice"]
        assert final["status"] == UserState.STOPPED.value
        assert final.get("removed") is True
    finally:
        manager.shutdown()


def test_unexpected_exception_publishes_error():
    manager = multiprocessing.Manager()
    status = manager.dict()
    try:
        rec = _make_recorder(
            ["boom"], status, lambda *a, **kw: None
        )
        # Trip removed as soon as we observe the error transition.
        def tripper():
            deadline = time.time() + 5
            while time.time() < deadline:
                entry = status.get("alice", {})
                if entry.get("status") == UserState.ERROR.value:
                    _set_removed(status, "alice")
                    return
                time.sleep(0.05)

        t = threading.Thread(target=tripper, daemon=True)
        t.start()
        rec.automatic_mode()
        t.join(timeout=2)

        final = status["alice"]
        assert final["status"] == UserState.STOPPED.value
        assert "boom" in final["message"]
    finally:
        manager.shutdown()


def test_offline_user_stays_waiting():
    manager = multiprocessing.Manager()
    status = manager.dict()
    try:
        rec = _make_recorder(
            ["offline"], status, lambda *a, **kw: None
        )

        poll_started = threading.Event()

        def tripper():
            deadline = time.time() + 10
            while time.time() < deadline:
                entry = status.get("alice", {})
                if entry.get("status") == UserState.WAITING.value:
                    # Wait until at least one poll cycle completed.
                    if not poll_started.is_set():
                        poll_started.set()
                        time.sleep(0.2)
                    _set_removed(status, "alice")
                    return
                time.sleep(0.05)

        t = threading.Thread(target=tripper, daemon=True)
        t.start()
        rec.automatic_mode()
        t.join(timeout=2)

        final = status["alice"]
        assert final["status"] == UserState.STOPPED.value
        assert final["message"] == ""
    finally:
        manager.shutdown()


def test_current_interval_reads_from_shared_value():
    """Each call to _current_interval reflects the latest Value."""
    manager = multiprocessing.Manager()
    status = manager.dict()
    interval_value = manager.Value("i", 1)
    try:
        config = RecorderConfig(
            mode=Mode.AUTOMATIC,
            user="alice",
            cookies={},
            automatic_interval=5,
            status_dict=status,
            username="alice",
            interval_value=interval_value,
        )
        rec = TikTokRecorder(config)
        assert rec._current_interval() == 1
        interval_value.value = 3
        assert rec._current_interval() == 3
    finally:
        manager.shutdown()


def test_current_interval_falls_back_to_config_when_no_shared_value():
    manager = multiprocessing.Manager()
    status = manager.dict()
    try:
        config = RecorderConfig(
            mode=Mode.AUTOMATIC,
            user="alice",
            cookies={},
            automatic_interval=7,
            status_dict=status,
            username="alice",
            interval_value=None,
        )
        rec = TikTokRecorder(config)
        assert rec._current_interval() == 7
    finally:
        manager.shutdown()
