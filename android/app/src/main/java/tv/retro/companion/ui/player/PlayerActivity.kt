package tv.retro.companion.ui.player

import android.os.Build
import android.os.Bundle
import android.view.View
import android.view.WindowInsets
import android.view.WindowInsetsController
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import tv.retro.companion.RetroTvApp
import tv.retro.companion.databinding.ActivityPlayerBinding

class PlayerActivity : AppCompatActivity() {

    private lateinit var binding: ActivityPlayerBinding
    private var player: ExoPlayer? = null
    private var channelNumber: Int = -1
    private var pollJob: Job? = null
    private var currentMediaKey: String? = null
    private val app get() = RetroTvApp.instance

    companion object {
        const val EXTRA_STREAM_URL = "extra_stream_url"
        const val EXTRA_TITLE = "extra_title"
        const val EXTRA_SUBTITLE = "extra_subtitle"
        const val EXTRA_CHANNEL_NUMBER = "extra_channel_number"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityPlayerBinding.inflate(layoutInflater)
        setContentView(binding.root)

        hideSystemUI()

        channelNumber = intent.getIntExtra(EXTRA_CHANNEL_NUMBER, -1)
        val streamUrl = intent.getStringExtra(EXTRA_STREAM_URL)
        val title = intent.getStringExtra(EXTRA_TITLE) ?: "Retro TV"
        val subtitle = intent.getStringExtra(EXTRA_SUBTITLE) ?: ""

        binding.tvPlayerTitle.text = title
        binding.tvPlayerSub.text = subtitle

        binding.btnClosePlayer.setOnClickListener {
            finish()
        }

        if (channelNumber > 0) {
            initLivePlayer(channelNumber)
        } else if (!streamUrl.isNullOrBlank()) {
            initStaticPlayer(streamUrl)
        }
    }

    private fun hideSystemUI() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            window.insetsController?.let { controller ->
                controller.hide(WindowInsets.Type.statusBars() or WindowInsets.Type.navigationBars())
                controller.systemBarsBehavior =
                    WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            }
        } else {
            @Suppress("DEPRECATION")
            window.decorView.systemUiVisibility = (
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                or View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                or View.SYSTEM_UI_FLAG_FULLSCREEN
            )
        }
    }

    private fun initStaticPlayer(url: String) {
        player = ExoPlayer.Builder(this).build().apply {
            playWhenReady = true
            val mediaItem = MediaItem.fromUri(url)
            setMediaItem(mediaItem)
            prepare()
        }
        binding.fullscreenPlayerView.player = player
    }

    private fun initLivePlayer(chNumber: Int) {
        player = ExoPlayer.Builder(this).build().apply {
            playWhenReady = true
            addListener(object : Player.Listener {
                override fun onPlaybackStateChanged(playbackState: Int) {
                    if (playbackState == Player.STATE_ENDED) {
                        syncLivePlayback(force = true)
                    }
                }
            })
        }
        binding.fullscreenPlayerView.player = player

        syncLivePlayback(force = true)
        startPolling()
    }

    private fun syncLivePlayback(force: Boolean = false) {
        if (channelNumber <= 0) return
        lifecycleScope.launch {
            val nowResp = app.api.getChannelNow(channelNumber) ?: return@launch
            if (!nowResp.ok) return@launch

            val changed = currentMediaKey != null && currentMediaKey != nowResp.mediaKey
            val ended = player?.playbackState == Player.STATE_ENDED
            val targetOffsetMs = (nowResp.offset * 1000).toLong()

            if (currentMediaKey == null || changed || (force && ended)) {
                currentMediaKey = nowResp.mediaKey
                if (nowResp.hasMedia) {
                    val streamUrl = "${app.api.getLiveStreamUrl(channelNumber)}?program=${nowResp.mediaKey ?: ""}"
                    val mediaItem = MediaItem.fromUri(streamUrl)
                    player?.setMediaItem(mediaItem, targetOffsetMs)
                    player?.prepare()
                    player?.play()
                } else {
                    player?.clearMediaItems()
                }
            } else if (!changed && player?.isPlaying == true) {
                val curPos = player?.currentPosition ?: 0L
                if (Math.abs(curPos - targetOffsetMs) > 12000L) {
                    player?.seekTo(targetOffsetMs)
                }
            }

            if (nowResp.entry != null) {
                binding.tvPlayerTitle.text = nowResp.entry.title
                val sub = buildString {
                    nowResp.entry.subtitle?.let { append(it) }
                    if (!nowResp.range.isNullOrBlank()) {
                        if (isNotEmpty()) append(" · ")
                        append(nowResp.range)
                    }
                }
                binding.tvPlayerSub.text = sub
            }
        }
    }

    private fun startPolling() {
        pollJob?.cancel()
        pollJob = lifecycleScope.launch {
            while (isActive) {
                delay(5000)
                syncLivePlayback()
            }
        }
    }

    override fun onStart() {
        super.onStart()
        player?.playWhenReady = true
        if (channelNumber > 0) startPolling()
    }

    override fun onStop() {
        super.onStop()
        player?.playWhenReady = false
        pollJob?.cancel()
    }

    override fun onDestroy() {
        super.onDestroy()
        pollJob?.cancel()
        player?.release()
        player = null
    }
}
