"""Run with python -m unittest tests.test_tvannouncer -v."""
import unittest
from unittest.mock import Mock, patch

import tvannouncer


class TvAnnouncerTests(unittest.TestCase):
    def test_new_play_uses_arena_tts_and_pipewire_audio(self):
        process = Mock()
        process.poll.side_effect = [None, 0, 0]
        process.communicate.return_value = (b"", b"")
        process.returncode = 0
        tvannouncer.set_game("nfl:live")
        player_volume = 70

        def ipc(command):
            nonlocal player_volume
            if command[0] == "get_property":
                return {"error": "success", "data": player_volume}
            player_volume = command[2]
            return {"error": "success"}

        with patch("tvannouncer.database.get_state", side_effect=lambda key, default: "0" if key == "muted" else "70"), \
             patch("tvannouncer.sports.synthesize_speech", return_value=(b"RIFF" + b"\0" * 100, "audio/wav")) as tts, \
             patch("tvannouncer.subprocess.Popen", return_value=process) as player, \
             patch("playback.audio_device", return_value="pipewire/alsa_output.test.hdmi-stereo"), \
             patch("playback._ipc", side_effect=ipc) as ipc_mock:
            self.assertTrue(tvannouncer.enqueue("nfl:live", "Keenum completes a pass."))
            worker = tvannouncer._thread
            worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertIsNone(tvannouncer._thread)
        tts.assert_called_once()
        self.assertIn("Keenum completes a pass.", tts.call_args.args[0])
        command = player.call_args.args[0]
        self.assertEqual(command[0], "pw-play")
        self.assertEqual(command[1:3], ["--target", "alsa_output.test.hdmi-stereo"])
        self.assertEqual(ipc_mock.call_count, 4)
        self.assertEqual(player_volume, 70)
        self.assertEqual(tvannouncer.status()["last_text"], "Keenum completes a pass.")

    def test_muted_receiver_does_not_announce(self):
        tvannouncer.set_game("nfl:muted")
        with patch("tvannouncer.database.get_state", return_value="1"), \
             patch("tvannouncer.sports.synthesize_speech") as tts:
            self.assertTrue(tvannouncer.enqueue("nfl:muted", "Run for a first down"))
            worker = tvannouncer._thread
            worker.join(timeout=5)
        tts.assert_not_called()

    def test_transient_arena_timeout_retries_current_play(self):
        process = Mock()
        process.poll.side_effect = [0, 0]
        process.communicate.return_value = (b"", b"")
        process.returncode = 0
        tvannouncer.set_game("nfl:retry")
        with patch("tvannouncer.database.get_state", side_effect=lambda key, default: "0" if key == "muted" else "70"), \
             patch("tvannouncer.sports.synthesize_speech", side_effect=[TimeoutError("timed out"),
                   (b"RIFF" + b"\0" * 100, "audio/wav")]) as tts, \
             patch("tvannouncer.subprocess.Popen", return_value=process), \
             patch("playback.audio_device", return_value="pipewire/alsa_output.test.hdmi-stereo"), \
             patch("playback._ipc"):
            tvannouncer.enqueue("nfl:retry", "Touchdown")
            worker = tvannouncer._thread
            worker.join(timeout=5)
        self.assertEqual(tts.call_count, 2)
        self.assertEqual(tvannouncer.status()["last_text"], "Touchdown")

    def test_zero_volume_does_not_announce(self):
        tvannouncer.set_game("nfl:zero")
        with patch("tvannouncer.database.get_state", side_effect=lambda key, default: "0"), \
             patch("tvannouncer.sports.synthesize_speech") as tts:
            tvannouncer.enqueue("nfl:zero", "Touchdown")
            worker = tvannouncer._thread
            worker.join(timeout=5)
        tts.assert_not_called()

    def test_low_volume_never_increases_background(self):
        process = Mock()
        process.poll.return_value = 0
        process.communicate.return_value = (b"", b"")
        process.returncode = 0
        tvannouncer.set_game("nfl:quiet")
        with patch("tvannouncer.database.get_state", side_effect=lambda key, default: "0" if key == "muted" else "10"), \
             patch("tvannouncer.sports.synthesize_speech", return_value=(b"RIFF" + b"\0" * 100, "audio/wav")), \
             patch("tvannouncer.subprocess.Popen", return_value=process) as player, \
             patch("playback.audio_device", return_value="pipewire/alsa_output.test.hdmi-stereo"), \
             patch("playback._ipc", return_value={"error": "success", "data": 10}) as ipc:
            tvannouncer.enqueue("nfl:quiet", "A quiet announcement")
            worker = tvannouncer._thread
            worker.join(timeout=5)
        self.assertIn("0.1", player.call_args.args[0])
        self.assertFalse(any(call.args[0][0] == "set_property" for call in ipc.call_args_list))


if __name__ == "__main__":
    unittest.main()
