package tv.retro.companion.ui.vod

import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.GridLayoutManager
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import kotlinx.coroutines.launch
import tv.retro.companion.R
import tv.retro.companion.RetroTvApp
import tv.retro.companion.data.model.VodEpisode
import tv.retro.companion.data.model.VodMovie
import tv.retro.companion.data.model.VodShow
import tv.retro.companion.databinding.FragmentVodBinding
import tv.retro.companion.ui.player.PlayerActivity

class VodFragment : Fragment() {

    private var _binding: FragmentVodBinding? = null
    private val binding get() = _binding!!
    private val app get() = RetroTvApp.instance

    private lateinit var vodAdapter: VodAdapter
    private var allMovies = listOf<VodMovie>()
    private var allShows = listOf<VodShow>()
    private var currentFilter = Filter.ALL

    enum class Filter { ALL, SHOWS, MOVIES }

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?): View {
        _binding = FragmentVodBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        setupRecyclerView()
        setupFilters()
        loadCatalog()

        binding.swipeRefreshVod.setOnRefreshListener {
            loadCatalog()
        }
    }

    private fun setupRecyclerView() {
        vodAdapter = VodAdapter { item ->
            when (item) {
                is VodItem.Movie -> showMovieDialog(item.movie)
                is VodItem.Show -> showSeriesDialog(item.show)
            }
        }
        binding.rvVod.layoutManager = GridLayoutManager(requireContext(), 2)
        binding.rvVod.adapter = vodAdapter
    }

    private fun setupFilters() {
        binding.chipAll.setOnClickListener {
            setFilter(Filter.ALL)
        }
        binding.chipShows.setOnClickListener {
            setFilter(Filter.SHOWS)
        }
        binding.chipMovies.setOnClickListener {
            setFilter(Filter.MOVIES)
        }
    }

    private fun setFilter(filter: Filter) {
        currentFilter = filter
        val amber = resources.getColor(R.color.retro_amber, null)
        val surface = resources.getColor(R.color.retro_surface_light, null)
        val darkBg = resources.getColor(R.color.retro_bg, null)
        val textSec = resources.getColor(R.color.text_secondary, null)

        binding.chipAll.setBackgroundColor(if (filter == Filter.ALL) amber else surface)
        binding.chipAll.setTextColor(if (filter == Filter.ALL) darkBg else textSec)

        binding.chipShows.setBackgroundColor(if (filter == Filter.SHOWS) amber else surface)
        binding.chipShows.setTextColor(if (filter == Filter.SHOWS) darkBg else textSec)

        binding.chipMovies.setBackgroundColor(if (filter == Filter.MOVIES) amber else surface)
        binding.chipMovies.setTextColor(if (filter == Filter.MOVIES) darkBg else textSec)

        updateList()
    }

    private fun loadCatalog() {
        binding.swipeRefreshVod.isRefreshing = true
        lifecycleScope.launch {
            val catalog = app.api.getVodCatalog()
            binding.swipeRefreshVod.isRefreshing = false

            if (catalog != null) {
                allMovies = catalog.movies
                allShows = catalog.shows
                val total = allMovies.size + allShows.size
                binding.tvVodCount.text = "$total TITLES"
                updateList()
            }
        }
    }

    private fun updateList() {
        val list = mutableListOf<VodItem>()
        when (currentFilter) {
            Filter.ALL -> {
                list.addAll(allShows.map { VodItem.Show(it) })
                list.addAll(allMovies.map { VodItem.Movie(it) })
            }
            Filter.SHOWS -> {
                list.addAll(allShows.map { VodItem.Show(it) })
            }
            Filter.MOVIES -> {
                list.addAll(allMovies.map { VodItem.Movie(it) })
            }
        }
        vodAdapter.submitList(list)
    }

    private fun showMovieDialog(movie: VodMovie) {
        val msg = buildString {
            if (!movie.description.isNullOrBlank()) {
                append(movie.description)
                append("\n\n")
            }
            append("Year: ").append(movie.year ?: "N/A")
            append(" · Genre: ").append(movie.genre ?: "N/A")
        }

        MaterialAlertDialogBuilder(requireContext())
            .setTitle(movie.title)
            .setMessage(msg)
            .setPositiveButton("PLAY ON TV") { _, _ ->
                lifecycleScope.launch {
                    val ok = app.api.playVod(movie.mediaId)
                    if (ok) {
                        Toast.makeText(requireContext(), "Playing ${movie.title} on TV", Toast.LENGTH_SHORT).show()
                    }
                }
            }
            .setNeutralButton("WATCH ON PHONE") { _, _ ->
                val streamUrl = app.api.getMediaFileStreamUrl(movie.mediaId)
                val intent = Intent(requireContext(), PlayerActivity::class.java).apply {
                    putExtra(PlayerActivity.EXTRA_STREAM_URL, streamUrl)
                    putExtra(PlayerActivity.EXTRA_TITLE, movie.title)
                    putExtra(PlayerActivity.EXTRA_SUBTITLE, "${movie.year ?: ""} ${movie.genre ?: ""}")
                }
                startActivity(intent)
            }
            .setNegativeButton("CANCEL", null)
            .show()
    }

    private fun showSeriesDialog(show: VodShow) {
        lifecycleScope.launch {
            val fullShow = app.api.getVodShow(show.id) ?: show
            val episodes = mutableListOf<VodEpisode>()
            fullShow.seasons?.forEach { season ->
                episodes.addAll(season.episodes)
            }

            if (episodes.isEmpty()) {
                Toast.makeText(requireContext(), "No episodes found for ${show.name}", Toast.LENGTH_SHORT).show()
                return@launch
            }

            val items = episodes.map { ep ->
                val s = String.format("S%02dE%02d", ep.season ?: 1, ep.episode ?: 1)
                "$s · ${ep.title ?: "Episode"}"
            }.toTypedArray()

            MaterialAlertDialogBuilder(requireContext())
                .setTitle(show.name)
                .setItems(items) { _, which ->
                    val chosen = episodes[which]
                    playOrWatchEpisode(show, chosen)
                }
                .setNegativeButton("CANCEL", null)
                .show()
        }
    }

    private fun playOrWatchEpisode(show: VodShow, ep: VodEpisode) {
        val s = String.format("S%02dE%02d", ep.season ?: 1, ep.episode ?: 1)
        val title = "${show.name} - $s ${ep.title ?: ""}"

        MaterialAlertDialogBuilder(requireContext())
            .setTitle(title)
            .setMessage(ep.description ?: "")
            .setPositiveButton("PLAY ON TV") { _, _ ->
                lifecycleScope.launch {
                    val ok = app.api.playVod(ep.mediaId)
                    if (ok) {
                        Toast.makeText(requireContext(), "Playing $title on TV", Toast.LENGTH_SHORT).show()
                    }
                }
            }
            .setNeutralButton("WATCH ON PHONE") { _, _ ->
                val streamUrl = app.api.getMediaFileStreamUrl(ep.mediaId)
                val intent = Intent(requireContext(), PlayerActivity::class.java).apply {
                    putExtra(PlayerActivity.EXTRA_STREAM_URL, streamUrl)
                    putExtra(PlayerActivity.EXTRA_TITLE, title)
                    putExtra(PlayerActivity.EXTRA_SUBTITLE, ep.description ?: "")
                }
                startActivity(intent)
            }
            .setNegativeButton("CANCEL", null)
            .show()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }
}
