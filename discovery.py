"""UDP discovery beacon for Retro TV companion app."""
import json
import logging
import socket
import threading
import config

log = logging.getLogger("retro-tv.discovery")

DISCOVERY_PORT = 5002
MAGIC_REQ = "RETRO_TV_DISCOVER"

_thread = None
_stop = threading.Event()


def _listen_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    except Exception:
        pass
    try:
        sock.bind(("", DISCOVERY_PORT))
    except Exception as e:
        log.warning("Could not bind UDP discovery port %d: %s", DISCOVERY_PORT, e)
        return

    sock.settimeout(1.0)
    log.info("UDP discovery service listening on port %d", DISCOVERY_PORT)

    while not _stop.is_set():
        try:
            data, addr = sock.recvfrom(1024)
            msg = data.decode("utf-8", errors="ignore").strip()
            if MAGIC_REQ in msg or "retro-tv" in msg.lower() or "retrotv" in msg.lower():
                payload = json.dumps({
                    "app": "retro-tv",
                    "name": "Retro TV",
                    "port": config.PORT,
                    "version": "1.0.0"
                }).encode("utf-8")
                sock.sendto(payload, addr)
        except socket.timeout:
            continue
        except Exception:
            if not _stop.is_set():
                log.debug("UDP discovery read error", exc_info=True)
            continue
    sock.close()


def start():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_listen_loop, name="udp-discovery", daemon=True)
    _thread.start()


def stop():
    _stop.set()
