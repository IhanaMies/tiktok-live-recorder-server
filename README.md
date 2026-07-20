<div align="center">

# TikTok Live Recorder 🎥

_TikTok Live Recorder is a tool for recording live streaming TikTok._

[![Telegram](https://img.shields.io/badge/Telegram-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://telegram.me/tiktokliverecorder)
![Python](https://img.shields.io/badge/python-3670A0?style=for-the-badge&logo=python&logoColor=ffdd54)
[![Licence](https://img.shields.io/github/license/Ileriayo/markdown-badges?style=for-the-badge)](./LICENSE)
[![Stars](https://img.shields.io/github/stars/Michele0303/tiktok-live-recorder?style=for-the-badge)](https://github.com/Michele0303/tiktok-live-recorder/stargazers)
[![Release](https://img.shields.io/github/v/release/Michele0303/tiktok-live-recorder?style=for-the-badge)](https://github.com/Michele0303/tiktok-live-recorder/releases/latest)
[![Docker Pulls](https://img.shields.io/docker/pulls/michele0303/tiktok-live-recorder?style=for-the-badge&logo=docker&logoColor=white)](https://hub.docker.com/r/michele0303/tiktok-live-recorder)

The TikTok Live Recorder is a tool designed to easily capture and save live streaming sessions from TikTok. It records both audio and video, allowing users to revisit and preserve engaging live content for later enjoyment and analysis. It's a valuable resource for creators, researchers, and anyone who wants to capture memorable moments from TikTok live streams.

![preview](https://i.ibb.co/YTHp5DT/image.png)

</div>

## Table of Contents

- [Installation](#installation)
- [Usage](#command-line-usage)
- [Guide](#guide)

## Installation

**Prerequisites:** [Git](https://git-scm.com), [Python 3.11+](https://www.python.org/downloads/), [FFmpeg](https://ffmpeg.org/download.html)

<details>
<summary>Windows 💻</summary>

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
git clone https://github.com/Michele0303/tiktok-live-recorder
cd tiktok-live-recorder
uv venv
uv sync
uv run python src/main.py -h
```

</details>

<details>
<summary>Linux 🐧</summary>

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/Michele0303/tiktok-live-recorder
cd tiktok-live-recorder
uv venv
uv sync
uv run python src/main.py -h
```

</details>

<details>
<summary>macOS 🍎</summary>

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
brew install ffmpeg
git clone https://github.com/Michele0303/tiktok-live-recorder
cd tiktok-live-recorder
uv venv
uv sync
uv run python src/main.py -h
```

</details>

<details>
<summary>Android — Termux 📱</summary>

Install Termux from [F-Droid](https://f-droid.org/packages/com.termux/) (avoid the Play Store version).

```bash
pkg update && pkg upgrade
pkg install git ffmpeg uv tur-repo
pkg uninstall python
pkg install python3.11
git clone https://github.com/Michele0303/tiktok-live-recorder
cd tiktok-live-recorder
uv venv
uv sync
uv run python src/main.py -h
```

</details>

<details>
<summary>Docker 🐳</summary>

```bash
sudo docker run \
  -v ./output:/output \
  michele0303/tiktok-live-recorder:latest \
  -output /output \
  -user <username>
```

</details>

## Command-Line Usage

```bash
uv run python src/main.py [options]
```

### Options

| Flag | Description |
|------|-------------|
| `-user <USERNAME>` | Username(s) to record. Separate multiple with commas. |
| `-url <URL>` | TikTok live URL to record from. |
| `-room_id <ROOM_ID>` | Room ID to record from. |
| `-mode <MODE>` | Recording mode: `manual`, `automatic`, `followers`. |
| `-automatic_interval <MIN>` | Polling interval in minutes (automatic mode only). |
| `-output <DIRECTORY>` | Directory where recordings will be saved. |
| `-duration <SECONDS>` | Stop recording after this many seconds. |
| `-proxy <URL>` | HTTP proxy to bypass regional restrictions. |
| `-bitrate <BITRATE>` | Output bitrate for post-processing (e.g. `1M`, `1000k`). |
| `-telegram` | Upload the recording to Telegram when done. Requires `telegram.json`. |
| `-no-update-check` | Skip the automatic update check on startup. |

### Recording Modes

- **`manual`** *(default)*: Records immediately if the user is currently live.
- **`automatic`**: Polls at regular intervals and records whenever the user goes live.
- **`followers`**: Automatically records live streams from all followed users.

## Guide

- [How to set cookies in cookies.json](https://github.com/Michele0303/tiktok-live-recorder/blob/main/docs/GUIDE.md#how-to-set-cookies)
- [How to get room_id](https://github.com/Michele0303/tiktok-live-recorder/blob/main/docs/GUIDE.md#how-to-get-room_id)
- [How to enable upload to Telegram](https://github.com/Michele0303/tiktok-live-recorder/blob/main/docs/GUIDE.md#how-to-enable-upload-to-telegram)

## Runtime control (multi-user automatic mode)

When started with `-mode automatic` and a comma-separated list of users
(`-user alice,bob,carol`), the recorder exposes a local HTTP control API on
`http://127.0.0.1:8723` so the monitored user set can be changed without
restarting the program.

### Endpoints

| Method | Path | Body | Description |
|--------|------|------|-------------|
| `GET`  | `/list`    | — | Returns the current users and their recording status. |
| `POST` | `/add`     | `{"user": "<username>"}` | Start recording a new user. |
| `POST` | `/remove`  | `{"user": "<username>"}` | Stop recording a user. |
| `GET`  | `/cookies` | — | Returns the current contents of `cookies.json`. |
| `POST` | `/cookies` | `{"sessionid_ss": "<value>"}` | Updates `sessionid_ss` in `cookies.json` (other keys are preserved). |

`GET /list` response shape:

```json
{
  "users": [
    {"user": "alice", "status": "waiting", "since": 1734567890.1, "message": ""},
    {"user": "bob",   "status": "live",    "since": 1734567891.4, "message": ""}
  ]
}
```

Status values: `waiting` (polling between checks), `live` (recording now),
`error` (last cycle raised), `stopped` (process exited).

The user list is persisted in `settings.json` in the working directory
and reloaded on the next startup, merged with the `-user` CLI argument.

### Examples

```bash
# Poll status from another terminal
curl -s http://127.0.0.1:8723/list | jq

# Add a user at runtime
curl -s -X POST -H 'Content-Type: application/json' \
     -d '{"user":"dave"}' http://127.0.0.1:8723/add

# Remove a user (let the current recording finish, then stop)
curl -s -X POST -H 'Content-Type: application/json' \
     -d '{"user":"dave"}' http://127.0.0.1:8723/remove

# Update sessionid_ss in cookies.json (other cookies are preserved)
curl -s -X POST -H 'Content-Type: application/json' \
     -d '{"sessionid_ss":"<new-session-id>"}' http://127.0.0.1:8723/cookies
```

### `list` subcommand

A convenience subcommand is also available to print a formatted table without
needing `curl`/`jq`:

```bash
uv run python src/main.py list
```

## Contributing

Contributions are welcome! Feel free to open an [issue](https://github.com/Michele0303/tiktok-live-recorder/issues) or submit a [pull request](https://github.com/Michele0303/tiktok-live-recorder/pulls).

## Legal ⚖️

This code is in no way affiliated with, authorized, maintained, sponsored or endorsed by TikTok or any of its affiliates or subsidiaries. Use at your own risk.
