"""Remote handling: keyboard-event based (2.4GHz HID remotes appear as keyboards).
Provides mapping storage + test hooks. Actual global key capture is done in browser/TV UI
(keydown listeners) plus optional evdev listener when run with input group (no root needed)."""
import logging
import database

log = logging.getLogger("retro-tv.remote")

ACTIONS = ["GUIDE", "CHANNEL_UP", "CHANNEL_DOWN", "PREV_CHANNEL", "INFO", "PLAY_PAUSE",
           "BACK", "UP", "DOWN", "LEFT", "RIGHT", "OK", "VOLUME_UP", "VOLUME_DOWN",
           "MUTE", "POWER_MENU", "HOME", "MENU"]

# sensible keyboard defaults incl. common HID remote keys
DEFAULTS = {
    "GUIDE": "g", "CHANNEL_UP": "PageUp", "CHANNEL_DOWN": "PageDown",
    "PREV_CHANNEL": "Backspace", "INFO": "i", "PLAY_PAUSE": " ",
    "BACK": "Escape", "UP": "ArrowUp", "DOWN": "ArrowDown", "LEFT": "ArrowLeft",
    "RIGHT": "ArrowRight", "OK": "Enter", "VOLUME_UP": "+", "VOLUME_DOWN": "-",
    "MUTE": "m", "POWER_MENU": "p", "HOME": "Home", "MENU": "Menu",
    "0": "0", "1": "1", "2": "2", "3": "3", "4": "4", "5": "5",
    "6": "6", "7": "7", "8": "8", "9": "9",
}

def get_mappings():
    con = database.connect()
    try:
        rows = {r["action"]: r["code"] for r in con.execute("SELECT action, code FROM remote_mappings")}
    finally:
        con.close()
    merged = dict(DEFAULTS)
    merged.update(rows)
    return merged

def set_mapping(action, code):
    con = database.connect()
    try:
        con.execute("INSERT INTO remote_mappings(action,code) VALUES(?,?) ON CONFLICT(action) DO UPDATE SET code=excluded.code", (action, code))
        con.commit()
    finally:
        con.close()

def list_input_devices():
    """List /dev/input devices readable by user (input group)."""
    import os, glob
    out = []
    for p in sorted(glob.glob("/dev/input/event*") + glob.glob("/dev/input/by-id/*")):
        try:
            out.append({"path": p, "readable": os.access(p, os.R_OK)})
        except Exception:
            pass
    return out
