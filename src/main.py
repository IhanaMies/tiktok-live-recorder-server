import json
import multiprocessing
import os
import signal
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 8723
PERSISTENCE_FILE = "settings.json"


def record_user(config):
    from core.tiktok_recorder import TikTokRecorder
    from utils.logger_manager import logger

    try:
        TikTokRecorder(config).run()
    except Exception as e:
        logger.error(f"{e}", exc_info=True)


def _build_config(args, mode, cookies, user=None):
    from utils.recorder_config import RecorderConfig

    return RecorderConfig(
        url=args.url,
        user=user,
        room_id=args.room_id,
        mode=mode,
        automatic_interval=args.automatic_interval,
        cookies=cookies,
        proxy=args.proxy,
        output=args.output,
        duration=args.duration,
        use_telegram=args.telegram,
        bitrate=args.bitrate,
        ffmpeg_path=args.ffmpeg_path,
    )


def _build_manager_config(args, mode, cookies, user, status_dict, interval_value):
    from utils.recorder_config import RecorderConfig

    return RecorderConfig(
        url=None,
        user=user,
        room_id=None,
        mode=mode,
        automatic_interval=args.automatic_interval,
        cookies=cookies,
        proxy=args.proxy,
        output=args.output,
        duration=args.duration,
        use_telegram=args.telegram,
        bitrate=args.bitrate,
        ffmpeg_path=args.ffmpeg_path,
        status_dict=status_dict,
        username=user,
        interval_value=interval_value,
    )


def _spawn_factory(args, mode, cookies, status_dict, interval_value):
    def spawn(user, _manager):
        config = _build_manager_config(
            args, mode, cookies, user, status_dict, interval_value
        )
        return multiprocessing.Process(
            target=record_user, args=(config,), name=f"recorder-{user}"
        )

    return spawn


def _run_manager_flow(args, mode, cookies, initial_users):
    """Multi-user automatic flow with runtime add/remove via the control API."""
    from utils.control_server import ControlServer
    from utils.user_store import UserStore

    manager = multiprocessing.Manager()
    try:
        status_dict = manager.dict()
        interval_value = manager.Value("i", args.automatic_interval)
        store = UserStore(
            path=PERSISTENCE_FILE,
            manager=manager,
            spawn_fn=_spawn_factory(
                args, mode, cookies, status_dict, interval_value
            ),
            status_dict=status_dict,
        )
        store.seed(initial_users)

        from utils.utils import read_cookies, write_cookies

        server = ControlServer(
            store,
            host=CONTROL_HOST,
            port=CONTROL_PORT,
            cookies_reader=read_cookies,
            cookies_writer=write_cookies,
            interval_getter=lambda: int(interval_value.value),
            interval_setter=lambda v: setattr(interval_value, "value", int(v)),
        )
        server.start()

        stop_event = threading.Event()

        def _on_signal(signum, _frame):
            from utils.logger_manager import logger

            logger.info(f"Received signal {signum}, shutting down...")
            stop_event.set()

        prev_int = signal.getsignal(signal.SIGINT)
        prev_term = signal.getsignal(signal.SIGTERM)
        try:
            signal.signal(signal.SIGINT, _on_signal)
            signal.signal(signal.SIGTERM, _on_signal)
        except (ValueError, OSError):
            # Not in main thread or platform without these signals.
            pass

        try:
            stop_event.wait()
        finally:
            server.stop()
            store.shutdown()
            try:
                signal.signal(signal.SIGINT, prev_int)
                signal.signal(signal.SIGTERM, prev_term)
            except (ValueError, OSError):
                pass
    finally:
        manager.shutdown()


def run_recordings(args, mode, cookies):
    from utils.enums import Mode

    if mode == Mode.AUTOMATIC and (isinstance(args.user, list) or args.user is None):
        initial_users = args.user if isinstance(args.user, list) else []
        _run_manager_flow(args, mode, cookies, initial_users)
        return

    if isinstance(args.user, list):
        processes = []
        for user in args.user:
            config = _build_config(args, mode, cookies, user=user)
            p = multiprocessing.Process(target=record_user, args=(config,))
            p.start()
            processes.append(p)
        try:
            for p in processes:
                p.join()
        except KeyboardInterrupt:
            print("\n[!] Ctrl-C detected.")
            try:
                for p in processes:
                    p.join()
            except KeyboardInterrupt:
                print("\n[!] Forcefully terminating all processes.")
                for p in processes:
                    if p.is_alive():
                        p.terminate()
    else:
        config = _build_config(args, mode, cookies, user=args.user)
        record_user(config)


def cmd_list(host=CONTROL_HOST, port=CONTROL_PORT, timeout=2.0):
    """Fetch /list from a running instance and print a formatted table."""
    url = f"http://{host}:{port}/list"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as ex:
        print(
            f"Could not connect to the control API at {url}: {ex.reason}.\n"
            "Is the recorder running in multi-user automatic mode?",
            file=sys.stderr,
        )
        return 2
    except (json.JSONDecodeError, OSError) as ex:
        print(f"Unexpected error while reading {url}: {ex}", file=sys.stderr)
        return 1

    users = data.get("users", [])
    if not users:
        print("(no users)")
        return 0

    rows = []
    for entry in users:
        since = float(entry.get("since", 0.0))
        since_str = (
            datetime.fromtimestamp(since, tz=timezone.utc).isoformat(
                timespec="seconds"
            )
            if since
            else "-"
        )
        rows.append(
            (
                str(entry.get("user", "")),
                str(entry.get("status", "")),
                since_str,
                str(entry.get("message", "")),
            )
        )

    widths = [
        max(len(r[0]) for r in rows),
        max(len(r[1]) for r in rows),
        max(len(r[2]) for r in rows),
        max(len(r[3]) for r in rows),
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format("USER", "STATUS", "SINCE (UTC)", "MESSAGE"))
    print(fmt.format(*["-" * w for w in widths]))
    for row in rows:
        print(fmt.format(*row))
    return 0


def main():
    from utils.args_handler import validate_and_parse_args
    from utils.utils import read_cookies
    from utils.logger_manager import logger
    from utils.custom_exceptions import TikTokRecorderError
    from utils.dependencies import check_ffmpeg
    from check_updates import check_updates

    if len(sys.argv) >= 2 and sys.argv[1] == "list":
        sys.exit(cmd_list())

    try:
        # validate and parse command line arguments
        args, mode = validate_and_parse_args()

        # check ffmpeg binary (supports custom path via -ffmpeg-path)
        check_ffmpeg(args.ffmpeg_path or "ffmpeg")

        # check for updates
        if args.update_check is True:
            logger.info("Checking for updates...\n")
            if check_updates():
                exit()
        else:
            logger.info("Skipped update check\n")

        # read cookies from the config file
        cookies = read_cookies()

        # run the recordings based on the parsed arguments
        run_recordings(args, mode, cookies)

    except TikTokRecorderError as ex:
        logger.error(f"Application Error: {ex}")

    except Exception as ex:
        logger.critical(f"Generic Error: {ex}", exc_info=True)


if __name__ == "__main__":
    # print the banner
    from utils.utils import banner

    banner()

    # check and install dependencies
    from utils.dependencies import check_and_install_dependencies

    check_and_install_dependencies()

    # set up signal handling for graceful shutdown
    multiprocessing.freeze_support()

    # run
    main()
