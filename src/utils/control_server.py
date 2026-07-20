import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from utils.logger_manager import logger
from utils.user_store import UserStore


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8723


class ControlServer:
    """Local HTTP control API for the multi-user automatic flow."""

    def __init__(
        self,
        store: UserStore,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        cookies_reader=None,
        cookies_writer=None,
        interval_getter=None,
        interval_setter=None,
    ):
        self._store = store
        self._host = host
        self._port = port
        self._cookies_reader = cookies_reader
        self._cookies_writer = cookies_writer
        self._interval_getter = interval_getter
        self._interval_setter = interval_setter
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._actual_port: int | None = None

    @property
    def port(self) -> int | None:
        return self._actual_port

    def start(self) -> bool:
        """Bind and start serving in a daemon thread. Returns False on bind error."""
        handler = _make_handler(
            self._store,
            self._cookies_reader,
            self._cookies_writer,
            self._interval_getter,
            self._interval_setter,
        )
        try:
            self._httpd = ThreadingHTTPServer((self._host, self._port), handler)
        except OSError as ex:
            logger.error(
                f"Control API could not bind {self._host}:{self._port}: {ex}. "
                "Recorder processes will continue without the control API."
            )
            return False
        self._actual_port = self._httpd.server_address[1]
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="control-server",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"Control API listening on http://{self._host}:{self._actual_port}")
        return True

    def stop(self, timeout: float = 5.0) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=timeout)


def _make_handler(
    store: UserStore,
    cookies_reader,
    cookies_writer,
    interval_getter,
    interval_setter,
):
    class Handler(BaseHTTPRequestHandler):
        # Silence the default per-request stderr access log.
        def log_message(self, format, *args):  # noqa: A002
            logger.debug(f"control: {self.address_string()} - {format % args}")

        def _write_json(self, status: HTTPStatus, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> tuple[dict | None, str | None]:
            length = self.headers.get("Content-Length")
            if length is None:
                return None, "missing Content-Length"
            try:
                length_int = int(length)
            except ValueError:
                return None, "invalid Content-Length"
            if length_int <= 0 or length_int > 4096:
                return None, "body too large"
            try:
                raw = self.rfile.read(length_int)
            except OSError as ex:
                return None, f"read error: {ex}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as ex:
                return None, f"invalid JSON: {ex}"
            if not isinstance(data, dict):
                return None, "body must be a JSON object"
            return data, None

        def do_GET(self):  # noqa: N802
            if self.path == "/list":
                self._write_json(HTTPStatus.OK, {"users": store.snapshot()})
                return
            if self.path == "/interval":
                if interval_getter is None:
                    self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                try:
                    value = interval_getter()
                except OSError as ex:
                    self._write_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        {"error": f"could not read interval: {ex}"},
                    )
                    return
                self._write_json(HTTPStatus.OK, {"interval": value})
                return
            if self.path == "/cookies":
                if cookies_reader is None:
                    self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                try:
                    data = cookies_reader()
                except OSError as ex:
                    self._write_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        {"error": f"could not read cookies: {ex}"},
                    )
                    return
                self._write_json(HTTPStatus.OK, data)
                return
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            ctype = self.headers.get("Content-Type", "")
            if not ctype.startswith("application/json"):
                self._write_json(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    {"error": "Content-Type must be application/json"},
                )
                return
            data, err = self._read_json()
            if err is not None:
                self._write_json(HTTPStatus.BAD_REQUEST, {"error": err})
                return

            if self.path == "/interval":
                if interval_setter is None:
                    self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                raw = data.get("interval")
                if isinstance(raw, bool) or not isinstance(raw, int):
                    self._write_json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "'interval' must be an integer"},
                    )
                    return
                if raw < 1:
                    self._write_json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "'interval' must be one second or more"},
                    )
                    return
                try:
                    print(f"HTTP: Set interval to {raw}")
                    interval_setter(raw)
                except OSError as ex:
                    self._write_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        {"error": f"could not write interval: {ex}"},
                    )
                    return
                self._write_json(HTTPStatus.OK, {"ok": True, "interval": raw})
                return

            if self.path == "/cookies":
                if cookies_writer is None:
                    self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                value = data.get("sessionid_ss")
                if not isinstance(value, str) or not value.strip():
                    self._write_json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "missing or empty 'sessionid_ss' field"},
                    )
                    return
                try:
                    current = cookies_reader() if cookies_reader is not None else {}
                except OSError as ex:
                    self._write_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        {"error": f"could not read cookies: {ex}"},
                    )
                    return
                current["sessionid_ss"] = value
                print("HTTP: Cookie set")
                try:
                    cookies_writer(current)
                except OSError as ex:
                    self._write_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        {"error": f"could not write cookies: {ex}"},
                    )
                    return
                self._write_json(
                    HTTPStatus.OK,
                    {"ok": True, "sessionid_ss": value},
                )
                return

            user = data.get("user")
            if not isinstance(user, str) or not user.strip():
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "missing or empty 'user' field"},
                )
                return
            if self.path == "/add":
                ok, message = store.add(user)
                if not ok and "already" in message:
                    self._write_json(HTTPStatus.CONFLICT, {"error": message})
                    return
                if not ok:
                    self._write_json(HTTPStatus.BAD_REQUEST, {"error": message})
                    return

                print(f"HTTP: Add user {user}")
                self._write_json(HTTPStatus.OK, {"ok": True, "user": message})
                return
            if self.path == "/remove":
                ok, message = store.remove(user)
                if not ok and "not being recorded" in message:
                    self._write_json(HTTPStatus.NOT_FOUND, {"error": message})
                    return
                if not ok:
                    self._write_json(HTTPStatus.BAD_REQUEST, {"error": message})
                    return

                print(f"HTTP: Remove user {user}")
                self._write_json(HTTPStatus.OK, {"ok": True, "user": message})
                return
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    return Handler
