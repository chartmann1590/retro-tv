"""Native XING WEI receiver controls, independent of browser keyboard focus."""
import glob
import json
import logging
import select
import threading
import time

import database

log = logging.getLogger('retro-tv.usbremote')
DEVICE_GLOB = '/dev/input/by-id/usb-XING_WEI_2.4G_USB_USB_Composite_Device*'
KEY_ACTIONS = {
    'KEY_UP': 'UP', 'KEY_DOWN': 'DOWN', 'KEY_LEFT': 'LEFT', 'KEY_RIGHT': 'RIGHT',
    'KEY_ENTER': 'OK', 'KEY_KPENTER': 'OK', 'KEY_OK': 'OK', 'KEY_SELECT': 'OK',
    'KEY_PAGEUP': 'CHANNEL_UP', 'KEY_CHANNELUP': 'CHANNEL_UP',
    'KEY_PAGEDOWN': 'CHANNEL_DOWN', 'KEY_CHANNELDOWN': 'CHANNEL_DOWN',
    'KEY_VOLUMEUP': 'VOLUME_UP', 'KEY_VOLUMEDOWN': 'VOLUME_DOWN', 'KEY_MUTE': 'MUTE',
    'KEY_PLAYPAUSE': 'PLAY_PAUSE', 'KEY_SPACE': 'PLAY_PAUSE',
    'KEY_PLAY': 'PLAY', 'KEY_PAUSE': 'PAUSE', 'KEY_STOPCD': 'LIVE', 'KEY_STOP': 'LIVE',
    'KEY_FASTFORWARD': 'FAST_FORWARD', 'KEY_NEXTSONG': 'FAST_FORWARD',
    'KEY_REWIND': 'REWIND', 'KEY_PREVIOUSSONG': 'REWIND',
    'KEY_SEARCH': 'SEARCH', 'KEY_FIND': 'SEARCH', 'KEY_F3': 'SEARCH',
    'KEY_EPG': 'GUIDE', 'KEY_MENU': 'GUIDE', 'KEY_COMPOSE': 'GUIDE',
    'KEY_HOMEPAGE': 'LIVE', 'KEY_HOME': 'LIVE', 'KEY_TV': 'LIVE',
    'KEY_BACK': 'BACK', 'KEY_ESC': 'BACK', 'KEY_BACKSPACE': 'BACK',
    'KEY_LAST': 'PREV_CHANNEL', 'KEY_INFO': 'INFO',
    'KEY_G': 'GUIDE', 'KEY_V': 'VOD', 'KEY_F': 'SEARCH', 'KEY_I': 'INFO',
    'KEY_M': 'MUTE', 'KEY_P': 'PLAY_PAUSE', 'KEY_EQUAL': 'VOLUME_UP',
    'KEY_MINUS': 'VOLUME_DOWN',
}
for _digit in '0123456789':
    KEY_ACTIONS['KEY_' + _digit] = _digit
    KEY_ACTIONS['KEY_KP' + _digit] = _digit
ACTIONS = sorted(set(KEY_ACTIONS.values()))
REPEAT_ACTIONS = {'UP', 'DOWN', 'LEFT', 'RIGHT', 'VOLUME_UP', 'VOLUME_DOWN',
                  'CHANNEL_UP', 'CHANNEL_DOWN', 'FAST_FORWARD', 'REWIND'}


def key_text(key, shift=False, caps=False):
    name = key.removeprefix('KEY_')
    if len(name) == 1 and name.isalpha():
        return name if shift != caps else name.lower()
    if len(name) == 1 and name.isdigit():
        return ')!@#$%^&*('[int(name)] if shift else name
    if name.startswith('KP') and name[2:].isdigit():
        return name[2:]
    pairs = {'SPACE': (' ', ' '), 'MINUS': ('-', '_'), 'EQUAL': ('=', '+'),
             'DOT': ('.', '>'), 'COMMA': (',', '<'), 'SLASH': ('/', '?'),
             'SEMICOLON': (';', ':'), 'APOSTROPHE': ("'", '"'),
             'LEFTBRACE': ('[', '{'), 'RIGHTBRACE': (']', '}'),
             'BACKSLASH': ('\\', '|'), 'GRAVE': ('`', '~')}
    return pairs.get(name, ('', ''))[int(shift)]


class Controller:
    def __init__(self):
        self.lock = threading.RLock()
        self.devices = []
        self.error = ''
        self.last_key = ''
        self.last_action = ''
        self.learning = None
        self.learn_until = 0
        self.digits = ''
        self.digit_until = 0
        self.search = None
        self.shifts = set()
        self.caps = False
        self.last_repeat = 0
        try:
            self.mappings = json.loads(database.get_setting('usb_remote_mappings', '{}'))
        except (ValueError, TypeError):
            self.mappings = {}
        if not isinstance(self.mappings, dict):
            self.mappings = {}

    def status(self):
        with self.lock:
            return {'connected': bool(self.devices), 'devices': list(self.devices),
                    'error': self.error, 'last_key': self.last_key, 'last_action': self.last_action,
                    'learning': self.learning if time.monotonic() < self.learn_until else None,
                    'mappings': {**KEY_ACTIONS, **self.mappings}, 'actions': ACTIONS}

    def learn(self, action):
        if action is not None and action not in ACTIONS:
            raise ValueError('Unknown USB remote action')
        with self.lock:
            self.learning = action
            self.learn_until = time.monotonic() + 30 if action else 0
            self.digits = ''

    def key(self, key, value, now=None):
        import playback
        import tvvod
        now = time.monotonic() if now is None else now
        if key in ('KEY_LEFTSHIFT', 'KEY_RIGHTSHIFT'):
            if value:
                self.shifts.add(key)
            else:
                self.shifts.discard(key)
            return
        if value not in (1, 2):
            return
        with self.lock:
            self.last_key = key
            if self.learning and now < self.learn_until:
                if value == 1:
                    self.mappings[key] = self.learning
                    database.set_setting('usb_remote_mappings', json.dumps(self.mappings))
                    self.last_action = self.learning
                    self.learning = None
                return
            self.learning = None
        if key == 'KEY_CAPSLOCK' and value == 1:
            self.caps = not self.caps
            return
        action = self.mappings.get(key, KEY_ACTIONS.get(key))
        if self.search is not None and not tvvod.is_visible():
            self.search = None
            playback.osd_message('', 1)
        if self.search is not None:
            if value == 2 and key != 'KEY_BACKSPACE':
                return
            if key in ('KEY_ENTER', 'KEY_KPENTER', 'KEY_OK'):
                query, self.search = self.search, None
                playback.osd_message('', 1)
                tvvod.nav('search', query=query)
            elif key in ('KEY_ESC', 'KEY_BACK'):
                self.search = None
                playback.osd_message('', 1)
            else:
                self.search = self.search[:-1] if key == 'KEY_BACKSPACE' else (self.search + key_text(key, bool(self.shifts), self.caps))[:100]
                self.search_prompt()
            return
        if value == 2:
            if action not in REPEAT_ACTIONS or now - self.last_repeat < .15:
                return
            self.last_repeat = now
        if action:
            self.last_action = action
            self.dispatch(action, now)

    def tick(self, now=None):
        now = time.monotonic() if now is None else now
        if self.digits and now >= self.digit_until:
            channel, self.digits = int(self.digits), ''
            self.tune(channel)

    def search_prompt(self):
        import playback
        playback.osd_message('Search titles: ' + self.search + '_\nType on the keyboard, then OK. Back cancels.', 300000)

    def tune(self, channel):
        import playback
        import tvguide
        import tvvod
        tvvod.close_vod()
        tvguide.close()
        result = playback.tune(channel, reason='api')
        if not result.get('ok'):
            playback.osd_message(result.get('error', 'Channel unavailable'))
        return result

    def dispatch(self, action, now=None):
        import playback
        import scheduler
        import tvguide
        import tvvod
        now = time.monotonic() if now is None else now
        if action.isdigit():
            self.digits = (self.digits + action)[-3:]
            self.digit_until = now + 1.5
            playback.osd_message('CH ' + self.digits, 1500)
            return
        if action == 'OK' and self.digits:
            channel, self.digits = int(self.digits), ''
            self.tune(channel)
            return
        if action == 'BACK' and self.digits:
            self.digits = ''
            playback.osd_message('', 1)
            return
        self.digits = ''
        if action == 'SEARCH':
            if not tvvod.is_visible():
                if not tvvod.open_vod().get('ok'):
                    return
            self.search = ''
            self.search_prompt()
        elif action in ('UP', 'DOWN', 'LEFT', 'RIGHT', 'OK', 'BACK') and tvvod.is_visible():
            tvvod.nav('select' if action == 'OK' else action.lower())
        elif action in ('UP', 'DOWN', 'LEFT', 'RIGHT', 'OK', 'BACK') and tvguide.is_visible():
            tvguide.navigate(action.lower())
        elif action == 'GUIDE':
            tvvod.close_vod()
            if tvguide.is_visible():
                tvguide.close()
            else:
                if not playback.mpv_alive():
                    playback.restore_last()
                tvguide.render(start=int(time.time() // 1800) * 1800)
        elif action == 'VOD':
            if tvvod.is_visible():
                tvvod.close_vod()
            else:
                tvvod.open_vod()
        elif action in ('LIVE', 'BACK'):
            tvvod.close_vod()
            tvguide.close()
            playback.stop_vod()
        elif action in ('CHANNEL_UP', 'CHANNEL_DOWN', 'UP', 'DOWN'):
            numbers = [c['number'] for c in scheduler.get_channels()]
            if numbers:
                current = playback.status()['current_channel']
                if current is None:
                    current = int(database.get_state('last_channel', '0') or 0)
                index = numbers.index(current) if current in numbers else -1
                step = 1 if action in ('CHANNEL_UP', 'UP') else -1
                self.tune(numbers[(index + step) % len(numbers)])
        elif action == 'PREV_CHANNEL':
            previous = database.get_state('prev_channel', '')
            if previous:
                self.tune(int(previous))
        elif action in ('PLAY_PAUSE', 'OK'):
            playback.pause_toggle()
        elif action in ('PLAY', 'PAUSE'):
            playback.set_paused(action == 'PAUSE')
        elif action in ('FAST_FORWARD', 'REWIND', 'LEFT', 'RIGHT'):
            playback.seek_relative(30 if action in ('FAST_FORWARD', 'RIGHT') else -30)
        elif action in ('VOLUME_UP', 'VOLUME_DOWN'):
            volume = int(database.get_state('volume', '80'))
            playback.set_volume(volume + (5 if action == 'VOLUME_UP' else -5))
            playback.set_mute(False)
            playback.osd_message('Volume ' + database.get_state('volume', '80'), 1500)
        elif action == 'MUTE':
            muted = database.get_state('muted', '0') != '1'
            playback.set_mute(muted)
            playback.osd_message('Muted' if muted else 'Sound on', 1500)
        elif action == 'INFO':
            state = playback.status()
            if state['is_vod']:
                info = state['vod_info'] or {}
                playback.osd_message(f"{info.get('title', '')}\n{info.get('subtitle', '')}", 5000)
            else:
                playback.show_info()


_controller = None
_thread = None
_stop = threading.Event()


def status():
    return _controller.status() if _controller else {'connected': False, 'devices': [], 'error': 'USB listener not started', 'actions': ACTIONS}


def learn(action):
    if _controller is None:
        raise ValueError('USB listener not started')
    _controller.learn(action)


def listen(controller, stop_event):
    try:
        from evdev import InputDevice, ecodes
    except ImportError:
        controller.error = 'Install the evdev dependency to enable the USB remote.'
        log.warning(controller.error)
        return
    devices = {}
    next_scan = 0
    try:
        while not stop_event.is_set():
            if time.monotonic() >= next_scan:
                next_scan = time.monotonic() + 3
                paths = [p for p in glob.glob(DEVICE_GLOB)
                         if p.endswith('-event-kbd') or p.endswith('-event-if03')]
                for path in paths:
                    if path in devices:
                        continue
                    device = None
                    try:
                        device = InputDevice(path)
                        if (device.info.vendor, device.info.product) != (0x1915, 0x1025):
                            device.close()
                            continue
                        device.grab()  # Prevent duplicate mpv/browser/desktop media-key actions.
                        devices[path] = device
                        controller.error = ''
                        log.info('USB remote connected: %s', path)
                    except OSError as error:
                        if device:
                            device.close()
                        controller.error = str(error)
                controller.devices = list(devices)
            if devices:
                ready = select.select(list(devices.values()), [], [], .1)[0]
            else:
                stop_event.wait(.1)
                ready = []
            for device in ready:
                try:
                    events = list(device.read())
                except OSError:
                    path = next(path for path, value in devices.items() if value is device)
                    devices.pop(path).close()
                    controller.devices = list(devices)
                    controller.shifts.clear()
                    controller.digits = ''
                    continue
                for event in events:
                    if event.type == ecodes.EV_KEY:
                        key = ecodes.KEY.get(event.code, '')
                        key = next((name for name in key if name in KEY_ACTIONS), key[-1]) if isinstance(key, (tuple, list)) else key
                        try:
                            controller.key(key, event.value)
                        except Exception:
                            log.exception('USB remote action failed: %s', key)
            try:
                controller.tick()
            except Exception:
                log.exception('USB channel entry failed')
    finally:
        for device in devices.values():
            device.close()  # Closing releases the exclusive grab, including on service stop.
        controller.devices = []


def start():
    global _controller, _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _controller = Controller()
    _thread = threading.Thread(target=listen, args=(_controller, _stop), name='usb-remote', daemon=True)
    _thread.start()
