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
        with patch("tvannouncer.database.get_state", side_effect=lambda key, default: "0" if key == "muted" else "70"), \
             patch("tvannouncer.sports.synthesize_speech", return_value=(b"RIFF" + b"\0" * 100, "audio/wav")) as tts, \
             patch("tvannouncer.subprocess.Popen", return_value=process) as player, \
             patch("playback._ipc") as ipc:
            self.assertTrue(tvannouncer.enqueue("nfl:live", "Keenum completes a pass."))
            tvannouncer._thread.join(timeout=5)
        self.assertFalse(tvannouncer._thread.is_alive())
        tts.assert_called_once()
        self.assertIn("Keenum completes a pass.", tts.call_args.args[0])
        self.assertEqual(player.call_args.args[0][0], "pw-play")
        self.assertEqual(ipc.call_count, 2)
        self.assertEqual(tvannouncer.status()["last_text"], "Keenum completes a pass.")

    def test_muted_receiver_does_not_announce(self):
        tvannouncer.set_game("nfl:muted")
        with patch("tvannouncer.database.get_state", return_value="1"), \
             patch("tvannouncer.sports.synthesize_speech") as tts:
            self.assertTrue(tvannouncer.enqueue("nfl:muted", "Run for a first down"))
            tvannouncer._thread.join(timeout=5)
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
             patch("playback._ipc"):
            tvannouncer.enqueue("nfl:retry", "Touchdown")
            tvannouncer._thread.join(timeout=5)
        self.assertEqual(tts.call_count, 2)
        self.assertEqual(tvannouncer.status()["last_text"], "Touchdown")


if __name__ == "__main__":
    unittest.main()
