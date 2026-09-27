"""USB controls without touching hardware: python -m unittest tests.test_usbremote -v."""
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import config
import database
import playback
import scheduler
import tvguide
import tvvod
import usbremote
from app import app


class UsbRemoteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for key, value in {'DB_PATH': os.path.join(self.tmp.name, 'test.db'),
                           'MPV_SOCKET': os.path.join(self.tmp.name, 'mpv.sock')}.items():
            patcher = patch.object(config, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.init_db()
        self.controller = usbremote.Controller()

    def test_recorded_device_buttons_have_correct_actions(self):
        expected = {'KEY_UP': 'UP', 'KEY_ENTER': 'OK', 'KEY_PAGEUP': 'CHANNEL_UP',
                    'KEY_PAGEDOWN': 'CHANNEL_DOWN', 'KEY_COMPOSE': 'GUIDE',
                    'KEY_HOMEPAGE': 'LIVE', 'KEY_BACK': 'BACK', 'KEY_SEARCH': 'SEARCH',
                    'KEY_PLAYPAUSE': 'PLAY_PAUSE', 'KEY_REWIND': 'REWIND',
                    'KEY_FASTFORWARD': 'FAST_FORWARD', 'KEY_MUTE': 'MUTE',
                    'KEY_NEXTSONG': 'FAST_FORWARD', 'KEY_PREVIOUSSONG': 'REWIND',
                    'KEY_VOLUMEUP': 'VOLUME_UP', 'KEY_VOLUMEDOWN': 'VOLUME_DOWN'}
        with patch.object(self.controller, 'dispatch') as dispatch:
            for key, action in expected.items():
                self.controller.key(key, 1, now=10)
                dispatch.assert_called_with(action, 10)
            count = dispatch.call_count
            self.controller.key('KEY_PLAYPAUSE', 0, now=10)
            self.controller.key('KEY_PLAYPAUSE', 2, now=10)
            self.assertEqual(dispatch.call_count, count)
            self.controller.key('KEY_VOLUMEUP', 2, now=10)
            self.controller.key('KEY_VOLUMEUP', 2, now=10.05)
            self.assertEqual(dispatch.call_count, count + 1)

    def test_digit_entry_ok_and_timeout_tune_once(self):
        with patch.object(self.controller, 'tune') as tune, patch.object(playback, 'osd_message'):
            self.controller.key('KEY_2', 1, now=1)
            self.controller.key('KEY_9', 1, now=1.2)
            self.controller.key('KEY_ENTER', 1, now=1.3)
            self.controller.tick(now=10)
            tune.assert_called_once_with(29)
            self.controller.key('KEY_3', 1, now=20)
            self.controller.tick(now=21)
            self.assertEqual(tune.call_count, 1)
            self.controller.tick(now=22)
            tune.assert_called_with(3)

    def test_full_keyboard_search_does_not_trigger_shortcuts(self):
        with patch.object(tvvod, 'is_visible', return_value=True), \
             patch.object(tvvod, 'nav') as nav, patch.object(playback, 'osd_message'), \
             patch.object(playback, 'set_mute') as mute:
            self.controller.key('KEY_SEARCH', 1)
            for key in ('KEY_M', 'KEY_A', 'KEY_T', 'KEY_R', 'KEY_I', 'KEY_X'):
                self.controller.key(key, 1)
            self.assertEqual(self.controller.search, 'matrix')
            self.controller.key('KEY_ENTER', 1)
            nav.assert_called_once_with('search', query='matrix')
            mute.assert_not_called()
            self.assertIsNone(self.controller.search)
        self.assertEqual(usbremote.key_text('KEY_1', True), '!')
        self.assertEqual(usbremote.key_text('KEY_A', caps=True), 'A')
        self.assertEqual(usbremote.key_text('KEY_A', True, True), 'a')

    def test_menu_navigation_uses_visible_overlay(self):
        with patch.object(tvvod, 'is_visible', return_value=True), \
             patch.object(tvvod, 'nav') as nav, patch.object(playback, 'pause_toggle') as pause:
            self.controller.key('KEY_ENTER', 1)
            nav.assert_called_once_with('select')
            pause.assert_not_called()
        with patch.object(tvvod, 'is_visible', return_value=False), \
             patch.object(tvguide, 'is_visible', return_value=True), \
             patch.object(tvguide, 'navigate') as nav:
            self.controller.key('KEY_RIGHT', 1)
            nav.assert_called_once_with('right')

    def test_transport_and_volume_share_existing_receiver_state(self):
        database.set_state('volume', 80)
        with patch.object(tvvod, 'is_visible', return_value=False), \
             patch.object(tvguide, 'is_visible', return_value=False), \
             patch.object(playback, 'seek_relative') as seek, \
             patch.object(playback, 'set_volume') as volume, \
             patch.object(playback, 'set_mute') as mute, \
             patch.object(playback, 'set_paused') as paused, \
             patch.object(playback, 'osd_message'):
            self.controller.key('KEY_FASTFORWARD', 1)
            seek.assert_called_with(30)
            self.controller.key('KEY_REWIND', 1)
            seek.assert_called_with(-30)
            self.controller.key('KEY_VOLUMEUP', 1)
            volume.assert_called_once_with(85)
            mute.assert_called_with(False)
            self.controller.key('KEY_PAUSE', 1)
            paused.assert_called_with(True)
            self.controller.key('KEY_PLAY', 1)
            paused.assert_called_with(False)

    def test_learning_persists_without_changing_browser_mapping(self):
        import remote
        before = remote.get_mappings()
        self.controller.learn('VOD')
        with patch.object(self.controller, 'dispatch') as dispatch:
            self.controller.key('KEY_F1', 1)
            dispatch.assert_not_called()
        self.assertEqual(usbremote.Controller().mappings['KEY_F1'], 'VOD')
        self.assertEqual(remote.get_mappings(), before)
        with patch.object(usbremote, '_controller', self.controller):
            client = app.test_client()
            self.assertEqual(client.get('/api/remote/usb').status_code, 200)
            self.assertEqual(client.post('/api/remote/usb', json={'action': 'invalid'}).status_code, 400)
            self.assertTrue(client.post('/api/remote/usb', json={'action': None}).get_json()['ok'])

    def test_listener_grabs_only_receiver_keys_and_decodes_mute_alias(self):
        from evdev import ecodes
        stop = Mock()
        stop.is_set.side_effect = [False, True]
        device = Mock()
        device.info.vendor, device.info.product = 0x1915, 0x1025
        device.read.return_value = [Mock(type=ecodes.EV_KEY, code=ecodes.KEY_MUTE, value=1)]
        paths = [usbremote.DEVICE_GLOB[:-1] + suffix for suffix in
                 ('-if02-event-kbd', '-if03-event-mouse', '-if03-mouse')]
        with patch.object(usbremote.glob, 'glob', return_value=paths), \
             patch('evdev.InputDevice', return_value=device) as open_device, \
             patch.object(usbremote.select, 'select', return_value=([device], [], [])), \
             patch.object(self.controller, 'key') as key:
            usbremote.listen(self.controller, stop)
        open_device.assert_called_once_with(paths[0])
        device.grab.assert_called_once()
        device.close.assert_called_once()
        key.assert_called_once_with('KEY_MUTE', 1)
        self.assertEqual(self.controller.devices, [])

    def test_guide_native_selection_and_phone_api_share_state(self):
        import time
        now = time.time()
        con = database.connect()
        try:
            for number in (2, 3):
                con.execute('INSERT INTO channels(number,name,sort_order) VALUES(?,?,?)', (number, f'Channel {number}', number))
                for offset in (0, 3600):
                    con.execute('''INSERT INTO schedule_entries(channel_number,start_ts,end_ts,kind,title,day)
                        VALUES(?,?,?,'movie',?,?)''', (number, now - 600 + offset, now + 3000 + offset, f'Movie {number}', scheduler.local_day()))
            con.commit()
        finally:
            con.close()
        with patch.object(tvguide, '_send', return_value={'error': 'success'}), \
             patch.object(playback, 'tune', return_value={'ok': True}) as tune:
            self.assertTrue(tvguide.render(2, start=now - 600))
            client = app.test_client()
            result = client.post('/api/tv-guide/nav', json={'action': 'down'}).get_json()
            self.assertEqual(result['channel'], 3)
            self.assertEqual(tvguide.get_status()['channel'], 3)
            self.controller.dispatch('OK')
            tune.assert_called_once_with(3, reason='api')
            self.assertFalse(tvguide.is_visible())
            self.assertEqual(client.post('/api/tv-guide/nav', json={'action': 'bad'}).status_code, 400)

    def test_listener_reopens_receiver_after_disconnect(self):
        stop = Mock()
        stop.is_set.side_effect = [False, False, True]
        first, second = Mock(), Mock()
        for device in (first, second):
            device.info.vendor, device.info.product = 0x1915, 0x1025
        first.read.side_effect = OSError('disconnected')
        second.read.return_value = []
        path = usbremote.DEVICE_GLOB[:-1] + '-event-if03'
        with patch.object(usbremote.glob, 'glob', return_value=[path]), \
             patch('evdev.InputDevice', side_effect=[first, second]) as open_device, \
             patch.object(usbremote.select, 'select', side_effect=[([first], [], []), ([second], [], [])]), \
             patch.object(usbremote.time, 'monotonic', side_effect=range(100, 2000, 100)):
            usbremote.listen(self.controller, stop)
        self.assertEqual(open_device.call_count, 2)
        first.close.assert_called_once()
        second.close.assert_called_once()
        second.grab.assert_called_once()


if __name__ == '__main__':
    unittest.main()
