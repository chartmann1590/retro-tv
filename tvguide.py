"""Native mpv guide overlay: the television needs no second browser/video player."""
import json
import socket
import config
import threading
import time

import scheduler

_lock = threading.RLock()
_visible = False
_channel = None
_entry_id = None
_start = None
_last_refresh = 0
_socket = None
_reader = None


def _send(command):
    # mpv owns overlays per IPC client: keep this connection alive until closing.
    global _socket, _reader
    try:
        if _socket is None:
            _socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            _socket.settimeout(2)
            _socket.connect(config.MPV_SOCKET)
            _reader = _socket.makefile("r")
        _socket.sendall((json.dumps({"command": command, "request_id": 42}) + "\n").encode())
        for line in _reader:
            response = json.loads(line)
            if response.get("request_id") == 42:
                return response
    except (OSError, ValueError):
        pass
    if _reader:
        _reader.close()
    if _socket:
        _socket.close()
    _socket = _reader = None
    return {"error": "Player unavailable"}



def _text(value):
    return str(value or "").replace("\\", " ").replace("{", "(").replace("}", ")").replace("\n", " ")


def _color(rgb):
    return rgb[4:6] + rgb[2:4] + rgb[0:2]


def render(channel=None, entry_id=None, start=None):
    import playback
    global _visible, _channel, _entry_id, _start, _last_refresh
    with _lock:
        if not _visible and channel is None:
            _channel = playback._current["channel"]
        _channel = channel if channel is not None else _channel
        _entry_id = entry_id if entry_id is not None else _entry_id
        _start = start if start is not None else _start
        begin = _start or int(time.time() // 1800) * 1800
        data = scheduler.guide_data(begin, hours=3)
        if not data:
            return False
        selected_row = next((i for i, row in enumerate(data) if row["channel"]["number"] == _channel), 0)
        rows = data[max(0, min(selected_row - 2, len(data) - 5)):][:5]
        row = data[selected_row]
        selected = next((e for e in row["entries"] if e["id"] == _entry_id), None)
        if selected is None:
            selected = next((e for e in row["entries"] if e["start_ts"] <= time.time() < e["end_ts"]), None)
        if selected is None and row["entries"]:
            selected = row["entries"][0]
        _channel = row["channel"]["number"]
        _entry_id = selected["id"] if selected else None
        ass = []

        def box(x, y, w, h, color):
            ass.append(f"{{\\an7\\pos({x},{y})\\bord0\\shad0\\1c&H{_color(color)}&\\p1}}m 0 0 l {w} 0 {w} {h} 0 {h}{{\\p0}}")

        def text(x, y, value, size=24, color="EFF2E9", clip=None):
            clipping = f"\\clip({clip[0]},{clip[1]},{clip[2]},{clip[3]})" if clip else ""
            ass.append(f"{{\\an7\\pos({x},{y})\\fnDejaVu Sans\\fs{size}\\bord0\\shad0\\1c&H{_color(color)}&{clipping}}}{_text(value)}")

        def clock(ts):
            from datetime import datetime
            return datetime.fromtimestamp(ts, scheduler.TZ).strftime("%-I:%M %p")

        box(0, 0, 1280, 720, "071A3B")
        box(40, 30, 1200, 65, "244980")
        text(62, 42, "RETRO TV  /  GUIDE", 31, "F8CB63")
        text(1020, 48, clock(time.time()), 25, "F8CB63")
        if selected:
            text(62, 111, f"CH {_channel:02d}   {clock(selected['start_ts'])} – {clock(selected['end_ts'])}", 20, "F8CB63")
            text(62, 144, selected["title"], 34, clip=(60, 140, 1210, 191))
            text(62, 194, selected.get("subtitle"), 23, clip=(60, 191, 1210, 233))
        text(62, 246, "CHANNEL", 18, "AAC0DF")
        width = 990
        for i in range(6):
            text(242 + i * width / 6, 246, clock(begin + i * 1800), 19, "F8CB63")
        for i, item in enumerate(rows):
            y = 280 + i * 72
            ch = item["channel"]
            box(40, y, 190, 69, "203D6B")
            text(52, y + 5, f"{ch['number']:02d}", 27, "F8CB63")
            text(52, y + 39, ch["name"], 17, clip=(40, y, 228, y + 69))
            for e in item["entries"]:
                left = 234 + int(max(0, e["start_ts"] - begin) / 10800 * width)
                right = 234 + int(min(10800, e["end_ts"] - begin) / 10800 * width)
                if right <= left:
                    continue
                active = e["id"] == _entry_id and ch["number"] == _channel
                box(left, y, right - left - 2, 69, "F8CB63" if active else "143369")
                color = "15274B" if active else "EFF2E9"
                clip = (left + 5, y, right - 4, y + 69)
                text(left + 9, y + 9, e["title"], 21, color, clip)
                text(left + 9, y + 39, e.get("subtitle"), 16, color, clip)
        text(62, 663, "SELECT A PROGRAM ON YOUR PHONE  •  TUNE TV TO WATCH  •  CLOSE GUIDE TO RETURN", 18, "F8CB63")
        result = _send(["osd-overlay", 42, "ass-events", "\n".join(ass), 1280, 720])
        _visible = result.get("error") == "success"
        _last_refresh = time.monotonic()
        return _visible


def close():
    import playback
    global _visible, _start, _entry_id
    with _lock:
        _visible = False
        _start = None
        _entry_id = None
        return _send(["osd-overlay", 42, "none", ""]).get("error") == "success"


def is_visible():
    return _visible


def refresh_if_visible():
    if _visible and time.monotonic() - _last_refresh > 4:
        render()
