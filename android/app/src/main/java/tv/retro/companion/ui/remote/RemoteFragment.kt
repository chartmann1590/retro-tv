package tv.retro.companion.ui.remote

import android.content.Context
import android.os.Build
import android.os.Bundle
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import tv.retro.companion.RetroTvApp
import tv.retro.companion.data.model.Channel
import tv.retro.companion.data.model.HdmiStatus
import tv.retro.companion.databinding.FragmentRemoteBinding

class RemoteFragment : Fragment() {

    private var _binding: FragmentRemoteBinding? = null
    private val binding get() = _binding!!
    private val app get() = RetroTvApp.instance

    private var pollJob: Job? = null
    private var digitBuffer = ""
    private var digitClearJob: Job? = null
    private var channels = listOf<Channel>()
    private var currentHdmiStatus: HdmiStatus? = null

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?): View {
        _binding = FragmentRemoteBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        setupKeypad()
        setupRockers()
        setupDpad()
        setupModeButtons()
        loadChannels()
    }

    private fun hapticFeedback() {
        try {
            val vibrator = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                val vm = requireContext().getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager
                vm?.defaultVibrator
            } else {
                @Suppress("DEPRECATION")
                requireContext().getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                vibrator?.vibrate(VibrationEffect.createOneShot(20, VibrationEffect.DEFAULT_AMPLITUDE))
            } else {
                @Suppress("DEPRECATION")
                vibrator?.vibrate(20)
            }
        } catch (_: Exception) {}
    }

    private fun loadChannels() {
        lifecycleScope.launch {
            channels = app.api.getChannels()
        }
    }

    private fun setupKeypad() {
        val digitButtons = mapOf(
            binding.key0 to "0", binding.key1 to "1", binding.key2 to "2",
            binding.key3 to "3", binding.key4 to "4", binding.key5 to "5",
            binding.key6 to "6", binding.key7 to "7", binding.key8 to "8",
            binding.key9 to "9"
        )

        digitButtons.forEach { (btn, digit) ->
            btn.setOnClickListener {
                hapticFeedback()
                appendDigit(digit)
            }
        }

        binding.keyClr.setOnClickListener {
            hapticFeedback()
            digitBuffer = ""
            updateDigitDisplay()
        }

        binding.keyGo.setOnClickListener {
            hapticFeedback()
            val chNum = digitBuffer.toIntOrNull()
            if (chNum != null) {
                tune(chNum)
                digitBuffer = ""
                updateDigitDisplay()
            }
        }
    }

    private fun appendDigit(d: String) {
        digitClearJob?.cancel()
        digitBuffer = (digitBuffer + d).takeLast(3)
        updateDigitDisplay()

        digitClearJob = lifecycleScope.launch {
            delay(1500)
            val chNum = digitBuffer.toIntOrNull()
            if (chNum != null) {
                tune(chNum)
            }
            digitBuffer = ""
            updateDigitDisplay()
        }
    }

    private fun updateDigitDisplay() {
        binding.lcdDigits.text = if (digitBuffer.isNotEmpty()) "CH $digitBuffer" else ""
    }

    private fun setupRockers() {
        binding.btnVolUp.setOnClickListener {
            hapticFeedback()
            stepVolume(5)
        }

        binding.btnVolDown.setOnClickListener {
            hapticFeedback()
            stepVolume(-5)
        }

        binding.btnMute.setOnClickListener {
            hapticFeedback()
            toggleMute()
        }

        binding.btnChUp.setOnClickListener {
            hapticFeedback()
            stepChannel(1)
        }

        binding.btnChDown.setOnClickListener {
            hapticFeedback()
            stepChannel(-1)
        }
    }

    private fun stepVolume(delta: Int) {
        val curVol = currentHdmiStatus?.getVolumeInt() ?: 80
        val newVol = (curVol + delta).coerceIn(0, 100)
        lifecycleScope.launch {
            app.api.setVolume(volume = newVol, muted = false)
            refreshStatus()
        }
    }

    private fun toggleMute() {
        val isMuted = currentHdmiStatus?.isMuted() ?: false
        lifecycleScope.launch {
            app.api.setVolume(muted = !isMuted)
            refreshStatus()
        }
    }

    private fun stepChannel(step: Int) {
        if (channels.isEmpty()) return
        val currentChNum = currentHdmiStatus?.channel ?: channels.first().number
        val index = channels.indexOfFirst { it.number == currentChNum }
        val nextIndex = if (index != -1) {
            (index + step).mod(channels.size)
        } else 0
        tune(channels[nextIndex].number)
    }

    private fun tune(channelNumber: Int) {
        lifecycleScope.launch {
            app.api.tune(channelNumber)
            refreshStatus()
        }
    }

    private fun setupDpad() {
        binding.dpadUp.setOnClickListener {
            hapticFeedback()
            lifecycleScope.launch {
                app.api.sendTvGuideNav("up")
            }
        }

        binding.dpadDown.setOnClickListener {
            hapticFeedback()
            lifecycleScope.launch {
                app.api.sendTvGuideNav("down")
            }
        }

        binding.dpadLeft.setOnClickListener {
            hapticFeedback()
            lifecycleScope.launch {
                app.api.sendTvGuideNav("left")
            }
        }

        binding.dpadRight.setOnClickListener {
            hapticFeedback()
            lifecycleScope.launch {
                app.api.sendTvGuideNav("right")
            }
        }

        binding.dpadOk.setOnClickListener {
            hapticFeedback()
            lifecycleScope.launch {
                app.api.sendTvGuideNav("ok")
            }
        }
    }

    private fun setupModeButtons() {
        binding.btnVod.setOnClickListener {
            hapticFeedback()
            lifecycleScope.launch {
                app.api.openTvVod()
                refreshStatus()
            }
        }

        binding.btnGuide.setOnClickListener {
            hapticFeedback()
            lifecycleScope.launch {
                app.api.openTvGuide()
                refreshStatus()
            }
        }

        binding.btnInfo.setOnClickListener {
            hapticFeedback()
            lifecycleScope.launch {
                app.api.showInfo()
            }
        }

        binding.btnLast.setOnClickListener {
            hapticFeedback()
            val prev = currentHdmiStatus?.prevChannel
            if (prev != null) {
                tune(prev)
            } else {
                Toast.makeText(requireContext(), "No previous channel", Toast.LENGTH_SHORT).show()
            }
        }

        binding.btnPause.setOnClickListener {
            hapticFeedback()
            lifecycleScope.launch {
                val status = app.api.setVolume(togglePause = true)
                if (status != null) updateLcd(status)
            }
        }

        binding.btnLive.setOnClickListener {
            hapticFeedback()
            lifecycleScope.launch {
                app.api.stopVod()
                refreshStatus()
            }
        }
    }

    private fun startPolling() {
        pollJob?.cancel()
        pollJob = lifecycleScope.launch {
            while (isActive) {
                refreshStatus()
                delay(2000)
            }
        }
    }

    private suspend fun refreshStatus() {
        val status = app.api.getHdmiStatus()
        if (status != null) {
            currentHdmiStatus = status
            updateLcd(status)
        } else {
            binding.lcdConnection.text = "RECONNECTING…"
        }
    }

    private fun updateLcd(st: HdmiStatus) {
        binding.lcdConnection.text = "● CONNECTED"

        if (st.isVod) {
            binding.lcdChannel.text = "OD"
            val info = st.vodInfo
            binding.lcdTitle.text = info?.get("title")?.toString() ?: "On Demand"
            binding.lcdSubtitle.text = info?.get("subtitle")?.toString() ?: ""
            binding.lcdMode.text = if (st.paused) "PAUSED" else "VOD"
        } else {
            binding.lcdChannel.text = st.channel?.let { String.format("%02d", it) } ?: "--"
            binding.lcdTitle.text = st.entry?.title ?: "Live TV"
            binding.lcdSubtitle.text = st.entry?.subtitle ?: ""
            binding.lcdMode.text = if (st.mpvAlive) {
                if (st.paused) "PAUSED" else "LIVE"
            } else "OFF AIR"
        }

        val vol = st.getVolumeInt()
        binding.lcdVolume.text = if (st.isMuted()) "MUTED" else "VOL $vol"
        binding.btnPause.text = if (st.paused) "▶ RESUME" else "Ⅱ PAUSE"
    }

    override fun onResume() {
        super.onResume()
        startPolling()
    }

    override fun onPause() {
        super.onPause()
        pollJob?.cancel()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }
}
