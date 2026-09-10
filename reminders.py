"""User-set reminders for a specific upcoming airing (picked from the guide or a
search result, not a whole favorite channel -- see scheduler.next_entries for that).

A background loop (run_loop) fires each reminder REMINDER_LEAD_SEC before it starts:
flashes a banner on the TV via mpv's OSD (playback.osd_message, auto-hides itself)
and, if Gotify is configured in Settings, pushes it to the phone. Best-effort
throughout, matching livecontent.py/metadata.py's style -- a failed push just logs
and moves on, it never blocks the TV banner.
"""
import logging
import threading
import time

import config
import database
import playback

log = logging.getLogger("retro-tv.reminders")


def create(entry_id):
    """Returns (entry_dict, error). Idempotent: re-requesting the same entry_id
    just returns the existing reminder rather than duplicating it."""
    con = database.connect()
    try:
        e = con.execute("SELECT * FROM schedule_entries WHERE id=?", (entry_id,)).fetchone()
        if not e:
            return None, "Program not found"
        if e["kind"] not in ("episode", "movie"):
            return None, "Reminders are only for shows and movies, not commercial breaks or off-air slots"
        if e["end_ts"] <= time.time():
            return None, "That airing has already ended"
        if con.execute("SELECT id FROM reminders WHERE entry_id=?", (entry_id,)).fetchone():
            return dict(e), None
        con.execute("""INSERT INTO reminders(entry_id,channel_number,title,subtitle,start_ts,created_ts,notified)
            VALUES(?,?,?,?,?,?,0)""",
            (e["id"], e["channel_number"], e["title"], e["subtitle"], e["start_ts"], time.time()))
        con.commit()
        return dict(e), None
    finally:
        con.close()


def list_upcoming():
    con = database.connect()
    try:
        return [dict(r) for r in con.execute(
            "SELECT * FROM reminders WHERE start_ts > ? ORDER BY start_ts", (time.time(),))]
    finally:
        con.close()


def delete(rid):
    con = database.connect()
    try:
        con.execute("DELETE FROM reminders WHERE id=?", (rid,))
        con.commit()
    finally:
        con.close()


def due_soon(within_sec=180):
    """For client-side in-page banners: reminders starting within the window,
    independent of the background loop's own notified flag -- clients dedupe by id
    locally, same pattern as the favorite-channel alert."""
    con = database.connect()
    try:
        ts = time.time()
        return [dict(r) for r in con.execute(
            "SELECT * FROM reminders WHERE start_ts > ? AND start_ts <= ? ORDER BY start_ts",
            (ts, ts + within_sec))]
    finally:
        con.close()


def _send_gotify(title, message):
    if database.get_setting("gotify_enabled", "0") != "1":
        return
    url = database.get_setting("gotify_url", "").rstrip("/")
    token = database.get_setting("gotify_token", "")
    if not url or not token:
        return
    try:
        import requests
        r = requests.post(f"{url}/message", params={"token": token},
                          json={"title": title, "message": message, "priority": 5}, timeout=8)
        if r.status_code >= 300:
            log.warning("Gotify push rejected: %s %s", r.status_code, r.text[:200])
    except Exception as e:
        log.warning("Gotify push failed: %s", e)


def _announce(r):
    mins = max(0, round((r["start_ts"] - time.time()) / 60))
    when = "now" if mins <= 0 else f"in {mins} min"
    subtitle = r["subtitle"] or ""
    text = f"⏰ REMINDER\n{r['title']}\n{subtitle}\nCH {r['channel_number']:02d} · {when}".strip()
    playback.osd_message(text, config.REMINDER_OSD_MS)
    _send_gotify(f"{r['title']} starting {when}",
                 f"Channel {r['channel_number']:02d} — {subtitle or r['title']}")
    log.info("Reminder fired: %r on ch=%s (%s)", r["title"], r["channel_number"], when)


def run_loop(stop_event=None):
    stop_event = stop_event or threading.Event()
    while not stop_event.is_set():
        try:
            con = database.connect()
            try:
                ts = time.time()
                due = [dict(r) for r in con.execute(
                    """SELECT * FROM reminders WHERE notified=0 AND start_ts <= ? AND start_ts > ?""",
                    (ts + config.REMINDER_LEAD_SEC, ts - 300))]
                for r in due:
                    con.execute("UPDATE reminders SET notified=1 WHERE id=?", (r["id"],))
                con.commit()
            finally:
                con.close()
            for r in due:
                try:
                    _announce(r)
                except Exception:
                    log.exception("Reminder announce failed for id=%s", r.get("id"))
        except Exception:
            log.exception("Reminder loop failed")
        stop_event.wait(config.REMINDER_CHECK_INTERVAL_SEC)
