package tv.retro.companion.ui.live

import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import androidx.recyclerview.widget.LinearLayoutManager
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import tv.retro.companion.RetroTvApp
import tv.retro.companion.data.model.Channel
import tv.retro.companion.databinding.FragmentLiveTvBinding
import tv.retro.companion.ui.player.PlayerActivity

class LiveTvFragment : Fragment() {

    private var _binding: FragmentLiveTvBinding? = null
    private val binding get() = _binding!!
    private val app get() = RetroTvApp.instance

    private var player: ExoPlayer? = null
    private var channels = listOf<Channel>()
    private var currentChannel: Channel? = null
    private lateinit var channelAdapter: ChannelAdapter
    private var pollJob: Job? = null

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?): View {
        _binding = FragmentLiveTvBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        initPlayer()
        setupRecyclerView()
        setupListeners()
        loadChannels()
    }

    private fun initPlayer() {
        val context = requireContext()
        player = ExoPlayer.Builder(context).build().apply {
            playWhenReady = true
            addListener(object : Player.Listener {
                override fun onPlaybackStateChanged(playbackState: Int) {
                    binding.playerLoading.visibility =
                        if (playbackState == Player.STATE_BUFFERING) View.VISIBLE else View.GONE
                }

                override fun onPlayerError(error: PlaybackException) {
                    binding.playerLoading.visibility = View.GONE
                }
            })
        }
        binding.playerView.player = player
    }

    private fun setupRecyclerView() {
        channelAdapter = ChannelAdapter { channel ->
            tuneChannel(channel.number)
        }
        binding.rvChannels.layoutManager = LinearLayoutManager(requireContext())
        binding.rvChannels.adapter = channelAdapter
    }

    private fun setupListeners() {
        binding.btnTuneTv.setOnClickListener {
            val ch = currentChannel ?: return@setOnClickListener
            lifecycleScope.launch {
                val ok = app.api.tune(ch.number)
                if (ok) {
                    Toast.makeText(requireContext(), "Tuned TV to CH ${ch.number} (${ch.name})", Toast.LENGTH_SHORT).show()
                } else {
                    Toast.makeText(requireContext(), "Could not tune TV", Toast.LENGTH_SHORT).show()
                }
            }
        }

        binding.btnFullscreen.setOnClickListener {
            val ch = currentChannel ?: return@setOnClickListener
            val intent = Intent(requireContext(), PlayerActivity::class.java).apply {
                putExtra(PlayerActivity.EXTRA_STREAM_URL, app.api.getLiveStreamUrl(ch.number))
                putExtra(PlayerActivity.EXTRA_TITLE, "CH ${String.format("%02d", ch.number)} · ${ch.name}")
                putExtra(PlayerActivity.EXTRA_SUBTITLE, binding.tvProgramTitle.text.toString())
            }
            startActivity(intent)
        }
    }

    private fun loadChannels() {
        lifecycleScope.launch {
            channels = app.api.getChannels()
            val hdmi = app.api.getHdmiStatus()
            val activeChNum = hdmi?.channel ?: channels.firstOrNull()?.number ?: 2

            channelAdapter.submitList(channels, activeChNum)
            tuneChannel(activeChNum)
        }
    }

    fun tuneChannel(channelNumber: Int) {
        val channel = channels.firstOrNull { it.number == channelNumber }
            ?: Channel(number = channelNumber, name = "CH $channelNumber")
        currentChannel = channel
        channelAdapter.setSelected(channelNumber)

        binding.tvChannelBadge.text = "CH " + String.format("%02d", channel.number)
        binding.tvChannelName.text = channel.name

        // Play stream via ExoPlayer
        val streamUrl = app.api.getLiveStreamUrl(channel.number)
        val mediaItem = MediaItem.fromUri(streamUrl)
        player?.setMediaItem(mediaItem)
        player?.prepare()

        updateNowPlaying()
        startMetadataPolling()
    }

    private fun updateNowPlaying() {
        lifecycleScope.launch {
            val hdmi = app.api.getHdmiStatus()
            if (hdmi != null && hdmi.channel == currentChannel?.number && hdmi.entry != null) {
                binding.tvProgramTitle.text = hdmi.entry.title
                binding.tvProgramSubtitle.text = hdmi.entry.subtitle ?: ""
            }
        }
    }

    private fun startMetadataPolling() {
        pollJob?.cancel()
        pollJob = lifecycleScope.launch {
            while (isActive) {
                updateNowPlaying()
                delay(10000)
            }
        }
    }

    override fun onStart() {
        super.onStart()
        player?.playWhenReady = true
    }

    override fun onStop() {
        super.onStop()
        player?.playWhenReady = false
        pollJob?.cancel()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        player?.release()
        player = null
        _binding = null
    }
}
