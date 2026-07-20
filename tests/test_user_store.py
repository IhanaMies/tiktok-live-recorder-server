import json
import multiprocessing
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.user_store import UserState, UserStore  # noqa: E402


class FakeProcess:
    def __init__(self, name):
        self.name = name
        self._alive = True
        self.exitcode = None
        self.started = False

    def start(self):
        self.started = True

    def is_alive(self):
        return self._alive

    def join(self, timeout=None):
        return None

    def terminate(self):
        self._alive = False
        self.exitcode = -1

    def kill(self):
        self._alive = False
        self.exitcode = -9


def make_spawn_fn(registry):
    def spawn(user, _manager):
        proc = FakeProcess(name=f"recorder-{user}")
        registry.append((user, proc))
        return proc

    return spawn


def make_store(tmp_path, registry):
    manager = multiprocessing.Manager()
    return (
        UserStore(
            path=tmp_path / "users.json",
            manager=manager,
            spawn_fn=make_spawn_fn(registry),
            status_dict=manager.dict(),
        ),
        manager,
    )


def test_seed_normalizes_and_dedupes(tmp_path):
    registry = []
    store, manager = make_store(tmp_path, registry)
    try:
        store.seed(["  @alice  ", "alice", "bob"])
        assert sorted(store.users()) == ["alice", "bob"]
        assert {u for u, _ in registry} == {"alice", "bob"}
    finally:
        manager.shutdown()


def test_persistence_round_trip(tmp_path):
    registry = []
    path = tmp_path / "users.json"
    manager1 = multiprocessing.Manager()
    store1 = UserStore(
        path=path,
        manager=manager1,
        spawn_fn=make_spawn_fn(registry),
        status_dict=manager1.dict(),
    )
    store1.seed(["alice", "bob"])
    manager1.shutdown()

    assert path.exists()
    data = json.loads(path.read_text())
    assert data == {"users": ["alice", "bob"]}

    # New store loads persisted users on seed.
    registry2 = []
    manager2 = multiprocessing.Manager()
    store2 = UserStore(
        path=path,
        manager=manager2,
        spawn_fn=make_spawn_fn(registry2),
        status_dict=manager2.dict(),
    )
    store2.seed([])
    try:
        assert sorted(store2.users()) == ["alice", "bob"]
    finally:
        manager2.shutdown()


def test_add_duplicate_returns_conflict(tmp_path):
    registry = []
    store, manager = make_store(tmp_path, registry)
    try:
        ok, _ = store.add("alice")
        assert ok
        ok, msg = store.add("alice")
        assert not ok
        assert "already" in msg
    finally:
        manager.shutdown()


def test_add_empty_user_rejected(tmp_path):
    registry = []
    store, manager = make_store(tmp_path, registry)
    try:
        ok, msg = store.add("   ")
        assert not ok
        assert "non-empty" in msg
    finally:
        manager.shutdown()


def test_remove_unknown_user_returns_404(tmp_path):
    registry = []
    store, manager = make_store(tmp_path, registry)
    try:
        ok, msg = store.remove("ghost")
        assert not ok
        assert "not being recorded" in msg
    finally:
        manager.shutdown()


def test_remove_waiting_user_terminates_immediately(tmp_path):
    registry = []
    store, manager = make_store(tmp_path, registry)
    try:
        store.seed(["alice"])
        _, proc = registry[0]
        # simulate a process that exits when terminated
        proc._alive = True
        store._status["alice"]["status"] = UserState.WAITING.value

        ok, _ = store.remove("alice")
        assert ok
        # process was terminated
        assert proc.exitcode == -1
        assert "alice" not in store.users()
        # Persistence file no longer contains the removed user.
        data = json.loads(store.persistence_path.read_text())
        assert "alice" not in data["users"]
    finally:
        manager.shutdown()


def test_remove_live_user_sets_flag_only(tmp_path):
    registry = []
    store, manager = make_store(tmp_path, registry)
    try:
        store.seed(["alice"])
        _, proc = registry[0]
        entry = store._status["alice"]
        entry["status"] = UserState.LIVE.value
        store._status["alice"] = entry

        ok, _ = store.remove("alice")
        assert ok
        # process not terminated yet (still recording)
        assert proc.is_alive()
        assert store._status["alice"]["removed"] is True
    finally:
        manager.shutdown()


def test_snapshot_includes_status(tmp_path):
    registry = []
    store, manager = make_store(tmp_path, registry)
    try:
        store.seed(["alice", "bob"])
        alice = store._status["alice"]
        alice["status"] = UserState.LIVE.value
        store._status["alice"] = alice
        bob = store._status["bob"]
        bob["status"] = UserState.ERROR.value
        store._status["bob"] = bob

        snap = {item["user"]: item for item in store.snapshot()}
        assert snap["alice"]["status"] == "live"
        assert snap["bob"]["status"] == "error"
        assert all("since" in v for v in snap.values())
        assert all("message" in v for v in snap.values())
    finally:
        manager.shutdown()


def test_corrupt_persistence_starts_empty(tmp_path):
    path = tmp_path / "users.json"
    path.write_text("{ this is not json")
    registry = []
    manager = multiprocessing.Manager()
    try:
        store = UserStore(
            path=path,
            manager=manager,
            spawn_fn=make_spawn_fn(registry),
            status_dict=manager.dict(),
        )
        store.seed([])
        assert store.users() == []
    finally:
        manager.shutdown()
