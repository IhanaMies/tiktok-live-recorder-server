import json
import multiprocessing
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.control_server import ControlServer  # noqa: E402
from utils.user_store import UserState, UserStore  # noqa: E402


class FakeProcess:
    def __init__(self):
        self._alive = True

    def start(self):
        pass

    def is_alive(self):
        return self._alive

    def join(self, timeout=None):
        return None

    def terminate(self):
        self._alive = False

    def kill(self):
        self._alive = False


def spawn_factory():
    def spawn(user, _manager):
        return FakeProcess()

    return spawn


def make_store(tmp_path, automatic_interval=60, interval_setter=None):
    manager = multiprocessing.Manager()
    store = UserStore(
        path=tmp_path / "users.json",
        manager=manager,
        spawn_fn=spawn_factory(),
        status_dict=manager.dict(),
        automatic_interval=automatic_interval,
        interval_setter=interval_setter,
    )
    return store, manager


def make_cookies_io(initial=None):
    """Return (reader, writer, backing_dict) for the cookies endpoint."""
    backing = dict(initial or {"sessionid_ss": "initial", "tt-target-idc": "eu"})
    return (lambda: dict(backing), lambda data: backing.update(data), backing)


def http_request(url, method="GET", body=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        return ex.code, json.loads(ex.read().decode("utf-8"))


def test_list_endpoint_returns_empty(tmp_path):
    store, manager = make_store(tmp_path)
    server = ControlServer(store, host="127.0.0.1", port=0)
    try:
        assert server.start() is True
        status, body = http_request(f"http://127.0.0.1:{server.port}/list")
        assert status == 200
        assert body == {"users": []}
    finally:
        server.stop()
        manager.shutdown()


def test_add_then_list_then_remove(tmp_path):
    store, manager = make_store(tmp_path)
    server = ControlServer(store, host="127.0.0.1", port=0)
    try:
        assert server.start() is True
        base = f"http://127.0.0.1:{server.port}"

        status, body = http_request(f"{base}/add", "POST", {"user": "alice"})
        assert status == 200
        assert body == {"ok": True, "user": "alice"}

        status, body = http_request(f"{base}/list")
        assert status == 200
        assert len(body["users"]) == 1
        assert body["users"][0]["user"] == "alice"
        assert body["users"][0]["status"] == UserState.WAITING.value

        status, body = http_request(f"{base}/remove", "POST", {"user": "alice"})
        assert status == 200

        # A user in 'waiting' state is terminated immediately and removed
        # from the active set; the list is now empty.
        status, body = http_request(f"{base}/list")
        assert status == 200
        assert body["users"] == []
    finally:
        server.stop()
        manager.shutdown()


def test_add_duplicate_returns_409(tmp_path):
    store, manager = make_store(tmp_path)
    server = ControlServer(store, host="127.0.0.1", port=0)
    try:
        assert server.start() is True
        base = f"http://127.0.0.1:{server.port}"

        http_request(f"{base}/add", "POST", {"user": "alice"})
        status, body = http_request(f"{base}/add", "POST", {"user": "alice"})
        assert status == 409
        assert "already" in body["error"]
    finally:
        server.stop()
        manager.shutdown()


def test_remove_unknown_returns_404(tmp_path):
    store, manager = make_store(tmp_path)
    server = ControlServer(store, host="127.0.0.1", port=0)
    try:
        assert server.start() is True
        base = f"http://127.0.0.1:{server.port}"
        status, body = http_request(f"{base}/remove", "POST", {"user": "ghost"})
        assert status == 404
        assert "not being recorded" in body["error"]
    finally:
        server.stop()
        manager.shutdown()


def test_missing_user_field_returns_400(tmp_path):
    store, manager = make_store(tmp_path)
    server = ControlServer(store, host="127.0.0.1", port=0)
    try:
        assert server.start() is True
        base = f"http://127.0.0.1:{server.port}"
        status, body = http_request(f"{base}/add", "POST", {})
        assert status == 400
        assert "user" in body["error"]
    finally:
        server.stop()
        manager.shutdown()


def test_unknown_path_returns_404(tmp_path):
    store, manager = make_store(tmp_path)
    server = ControlServer(store, host="127.0.0.1", port=0)
    try:
        assert server.start() is True
        status, body = http_request(f"http://127.0.0.1:{server.port}/nope")
        assert status == 404
        assert "not found" in body["error"]
    finally:
        server.stop()
        manager.shutdown()


def test_non_json_content_type_returns_415(tmp_path):
    store, manager = make_store(tmp_path)
    server = ControlServer(store, host="127.0.0.1", port=0)
    try:
        assert server.start() is True
        url = f"http://127.0.0.1:{server.port}/add"
        req = urllib.request.Request(
            url,
            data=b"user=alice",
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as ex:
            assert ex.code == 415
    finally:
        server.stop()
        manager.shutdown()


def test_get_cookies_returns_current(tmp_path):
    store, manager = make_store(tmp_path)
    reader, writer, backing = make_cookies_io(
        {"sessionid_ss": "abc", "tt-target-idc": "eu"}
    )
    server = ControlServer(
        store,
        host="127.0.0.1",
        port=0,
        cookies_reader=reader,
        cookies_writer=writer,
    )
    try:
        assert server.start() is True
        status, body = http_request(f"http://127.0.0.1:{server.port}/cookies")
        assert status == 200
        assert body == {"sessionid_ss": "abc", "tt-target-idc": "eu"}
    finally:
        server.stop()
        manager.shutdown()


def test_post_cookies_updates_sessionid_ss(tmp_path):
    store, manager = make_store(tmp_path)
    reader, writer, backing = make_cookies_io(
        {"sessionid_ss": "old", "tt-target-idc": "eu"}
    )
    server = ControlServer(
        store,
        host="127.0.0.1",
        port=0,
        cookies_reader=reader,
        cookies_writer=writer,
    )
    try:
        assert server.start() is True
        base = f"http://127.0.0.1:{server.port}"
        status, body = http_request(
            f"{base}/cookies", "POST", {"sessionid_ss": "new-value"}
        )
        assert status == 200
        assert body == {"ok": True, "sessionid_ss": "new-value"}
        # Writer received a dict containing the new value and preserved other keys.
        assert backing == {
            "sessionid_ss": "new-value",
            "tt-target-idc": "eu",
        }

        # GET reflects the update.
        status, body = http_request(f"{base}/cookies")
        assert status == 200
        assert body["sessionid_ss"] == "new-value"
    finally:
        server.stop()
        manager.shutdown()


def test_post_cookies_missing_field_returns_400(tmp_path):
    store, manager = make_store(tmp_path)
    reader, writer, _ = make_cookies_io()
    server = ControlServer(
        store,
        host="127.0.0.1",
        port=0,
        cookies_reader=reader,
        cookies_writer=writer,
    )
    try:
        assert server.start() is True
        status, body = http_request(
            f"http://127.0.0.1:{server.port}/cookies", "POST", {}
        )
        assert status == 400
        assert "sessionid_ss" in body["error"]
    finally:
        server.stop()
        manager.shutdown()


def test_cookies_endpoint_absent_when_reader_not_provided(tmp_path):
    store, manager = make_store(tmp_path)
    server = ControlServer(store, host="127.0.0.1", port=0)
    try:
        assert server.start() is True
        status, _ = http_request(f"http://127.0.0.1:{server.port}/cookies")
        assert status == 404
    finally:
        server.stop()
        manager.shutdown()


def make_interval_io(initial=60):
    """Return (getter, setter, backing_list) for the interval endpoint."""
    backing = [int(initial)]
    return (lambda: backing[0], lambda v: backing.__setitem__(0, int(v)), backing)


def test_get_interval_returns_current(tmp_path):
    store, manager = make_store(tmp_path)
    getter, setter, _ = make_interval_io(7)
    server = ControlServer(
        store,
        host="127.0.0.1",
        port=0,
        interval_getter=getter,
        interval_setter=setter,
    )
    try:
        assert server.start() is True
        status, body = http_request(f"http://127.0.0.1:{server.port}/interval")
        assert status == 200
        assert body == {"interval": 7}
    finally:
        server.stop()
        manager.shutdown()


def test_post_interval_updates_value_and_settings(tmp_path):
    _, setter, backing = make_interval_io(60)
    store, manager = make_store(tmp_path, automatic_interval=60, interval_setter=setter)
    store.seed([])
    server = ControlServer(
        store,
        host="127.0.0.1",
        port=0,
        interval_getter=lambda: store.automatic_interval,
        interval_setter=store.set_automatic_interval,
    )
    try:
        assert server.start() is True
        base = f"http://127.0.0.1:{server.port}"
        status, body = http_request(f"{base}/interval", "POST", {"interval": 120})
        assert status == 200
        assert body == {"ok": True, "interval": 120}
        assert backing[0] == 120
        assert json.loads(store.persistence_path.read_text()) == {
            "users": [],
            "automatic_interval": 120,
        }

        status, body = http_request(f"{base}/interval")
        assert status == 200
        assert body["interval"] == 120
    finally:
        server.stop()
        manager.shutdown()


def test_post_interval_rejects_zero_and_negative(tmp_path):
    store, manager = make_store(tmp_path)
    getter, setter, _ = make_interval_io(5)
    server = ControlServer(
        store,
        host="127.0.0.1",
        port=0,
        interval_getter=getter,
        interval_setter=setter,
    )
    try:
        assert server.start() is True
        base = f"http://127.0.0.1:{server.port}"
        for value in (0, -1):
            status, body = http_request(f"{base}/interval", "POST", {"interval": value})
            assert status == 400
            assert "one second or more" in body["error"]
    finally:
        server.stop()
        manager.shutdown()


def test_post_interval_rejects_non_integer(tmp_path):
    store, manager = make_store(tmp_path)
    getter, setter, _ = make_interval_io(5)
    server = ControlServer(
        store,
        host="127.0.0.1",
        port=0,
        interval_getter=getter,
        interval_setter=setter,
    )
    try:
        assert server.start() is True
        base = f"http://127.0.0.1:{server.port}"
        for value in ("5", 5.5, True, None):
            status, body = http_request(f"{base}/interval", "POST", {"interval": value})
            assert status == 400
            assert "integer" in body["error"]
    finally:
        server.stop()
        manager.shutdown()


def test_interval_endpoint_absent_when_not_provided(tmp_path):
    store, manager = make_store(tmp_path)
    server = ControlServer(store, host="127.0.0.1", port=0)
    try:
        assert server.start() is True
        status, _ = http_request(f"http://127.0.0.1:{server.port}/interval")
        assert status == 404
    finally:
        server.stop()
        manager.shutdown()
