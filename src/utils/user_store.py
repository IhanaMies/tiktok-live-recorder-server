import json
import multiprocessing
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Callable

from utils.logger_manager import logger


class UserState(str, Enum):
    WAITING = "waiting"
    LIVE = "live"
    ERROR = "error"
    STOPPED = "stopped"


SpawnFn = Callable[[str, "multiprocessing.Manager"], multiprocessing.Process]


class UserStore:
    """
    Owns the set of monitored users, their recorder processes, and the shared
    status dictionary that recorders publish into.

    Persistence file (`path`) stores the username list and automatic interval
    as JSON so they survive restarts. Status is in-memory only.
    """

    def __init__(
        self,
        path: Path,
        manager: multiprocessing.Manager,
        spawn_fn: SpawnFn,
        status_dict: dict | None = None,
        watchdog_interval: float = 1.0,
        automatic_interval: int = 60,
        interval_setter: Callable[[int], None] | None = None,
    ):
        self._path = Path(path)
        self._manager = manager
        self._status: dict = status_dict if status_dict is not None else manager.dict()
        self._processes: dict[str, multiprocessing.Process] = {}
        self._spawn_fn = spawn_fn
        self._lock = threading.Lock()
        self._watchdog_interval = watchdog_interval
        self._stop_event = threading.Event()
        self._watchdog_thread: threading.Thread | None = None
        self._automatic_interval = automatic_interval
        self._interval_setter = interval_setter

    @property
    def status(self) -> dict:
        return self._status

    @property
    def persistence_path(self) -> Path:
        return self._path

    @property
    def automatic_interval(self) -> int:
        return self._automatic_interval

    def _set_automatic_interval(self, value: int) -> None:
        if self._interval_setter is not None:
            self._interval_setter(value)
        self._automatic_interval = value

    def set_automatic_interval(self, value: int) -> None:
        with self._lock:
            self._set_automatic_interval(value)
            self._persist()

    def _load_persisted(self) -> list[str]:
        if not self._path.exists():
            return []
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            users = data.get("users", [])
            if not isinstance(users, list):
                logger.warning(f"Ignoring {self._path}: 'users' is not a list.")
                users = []
            interval = data.get("automatic_interval")
            if interval is not None:
                if (
                    isinstance(interval, bool)
                    or not isinstance(interval, int)
                    or interval < 1
                ):
                    logger.warning(
                        f"Ignoring invalid 'automatic_interval' in {self._path}."
                    )
                else:
                    self._set_automatic_interval(interval)
            return [str(u).lstrip("@").strip() for u in users if str(u).strip()]
        except (OSError, json.JSONDecodeError) as ex:
            logger.warning(
                f"Could not load {self._path}: {ex}. Starting with empty user set."
            )
            return []

    def _persist(self) -> None:
        users = sorted(self._processes.keys())
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(
                    {
                        "users": users,
                        "automatic_interval": self._automatic_interval,
                    },
                    f,
                )
            tmp.replace(self._path)
        except OSError as ex:
            logger.warning(f"Could not persist {self._path}: {ex}")

    def _normalize(self, user: str) -> str:
        return user.strip().lstrip("@")

    def seed(self, users: list[str]) -> None:
        """Spawn recorder processes for the union of `users` and persisted users."""
        seen: set[str] = set()
        for raw in self._load_persisted() + list(users):
            user = self._normalize(raw)
            if not user or user in seen:
                continue
            seen.add(user)
            self._spawn(user)
        self._persist()
        self._ensure_watchdog()

    def add(self, user: str) -> tuple[bool, str]:
        """
        Add a new user. Returns (ok, message).
        ok=False for validation errors or duplicates; ok=True on success.
        """
        user = self._normalize(user)
        if not user:
            return False, "user must be a non-empty string"
        with self._lock:
            if user in self._processes:
                return False, f"user '{user}' is already being recorded"
            self._spawn(user)
            self._persist()
        return True, user

    def remove(self, user: str) -> tuple[bool, str]:
        """
        Mark a user as removed. The recorder will exit gracefully after its
        current recording finishes. If the recorder is currently in 'waiting'
        state, the process is terminated immediately.
        """
        user = self._normalize(user)
        if not user:
            return False, "user must be a non-empty string"
        with self._lock:
            if user not in self._processes:
                return False, f"user '{user}' is not being recorded"
            entry = self._status.get(user, {})
            entry["removed"] = True
            self._status[user] = entry
            proc = self._processes[user]
        if not proc.is_alive():
            self._cleanup_user(user)
            return True, user
        entry = self._status.get(user, {})
        if entry.get("status") != UserState.LIVE.value:
            proc.terminate()
            proc.join(timeout=2)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=2)
            self._cleanup_user(user)
        return True, user

    def snapshot(self) -> list[dict]:
        """Return a JSON-safe list of current user statuses, sorted by username."""
        items: list[dict] = []
        with self._lock:
            known = sorted(self._processes.keys())
        for user in known:
            entry = self._status.get(
                user,
                {"status": UserState.WAITING.value, "since": 0.0, "message": ""},
            )
            items.append(
                {
                    "user": user,
                    "status": entry.get("status", UserState.WAITING.value),
                    "since": float(entry.get("since", 0.0)),
                    "message": entry.get("message", ""),
                }
            )
        return items

    def has_alive_processes(self) -> bool:
        """Return True if at least one recorder process is still running."""
        with self._lock:
            processes = list(self._processes.values())
        return any(p.is_alive() for p in processes)

    def users(self) -> list[str]:
        """Return the sorted list of users currently being managed."""
        with self._lock:
            return sorted(self._processes.keys())

    def shutdown(self, timeout: float = 10.0) -> None:
        """Set removed=True for all users, join processes, terminate survivors."""
        self._stop_event.set()
        with self._lock:
            users = list(self._processes.keys())
        for user in users:
            entry = self._status.get(user, {})
            entry["removed"] = True
            self._status[user] = entry
        deadline = time.time() + timeout
        for user in users:
            proc = self._processes.get(user)
            if proc is None or not proc.is_alive():
                self._cleanup_user(user)
                continue
            remaining = max(0.0, deadline - time.time())
            proc.join(timeout=remaining)
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=2)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=2)
            self._cleanup_user(user)
        if self._watchdog_thread is not None:
            self._watchdog_thread.join(timeout=2)

    def _spawn(self, user: str) -> None:
        self._status[user] = {
            "status": UserState.WAITING.value,
            "since": time.time(),
            "message": "",
            "removed": False,
        }
        proc = self._spawn_fn(user, self._manager)
        proc.start()
        self._processes[user] = proc

    def _cleanup_user(self, user: str) -> None:
        with self._lock:
            self._processes.pop(user, None)
        entry = self._status.get(user)
        if entry is not None:
            entry["status"] = UserState.STOPPED.value
            entry["since"] = time.time()
            self._status[user] = entry
        self._persist()

    def _ensure_watchdog(self) -> None:
        if self._watchdog_thread is not None and self._watchdog_thread.is_alive():
            return
        self._stop_event.clear()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop, name="user-store-watchdog", daemon=True
        )
        self._watchdog_thread.start()

    def _watchdog_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                users = list(self._processes.items())
            for user, proc in users:
                if proc.is_alive():
                    continue
                entry = self._status.get(user, {})
                if entry.get("status") == UserState.STOPPED.value:
                    continue
                if entry.get("removed"):
                    self._cleanup_user(user)
                    continue
                exit_code = proc.exitcode
                self._status[user] = {
                    "status": UserState.ERROR.value,
                    "since": time.time(),
                    "message": f"process exited (code={exit_code})",
                    "removed": entry.get("removed", False),
                }
            self._stop_event.wait(self._watchdog_interval)
