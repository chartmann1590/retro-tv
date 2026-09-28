package tv.retro.companion.ui.guide

import android.content.Intent
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import kotlinx.coroutines.launch
import tv.retro.companion.RetroTvApp
import tv.retro.companion.data.model.GuideChannelRow
import tv.retro.companion.data.model.GuideEntry
import tv.retro.companion.databinding.FragmentGuideBinding
import tv.retro.companion.ui.main.MainActivity
import tv.retro.companion.ui.player.PlayerActivity
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class GuideFragment : Fragment() {

    private var _binding: FragmentGuideBinding? = null
    private val binding get() = _binding!!
    private val app get() = RetroTvApp.instance

    private lateinit var guideAdapter: GuideChannelAdapter
    private var allGuideRows = listOf<GuideChannelRow>()

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?): View {
        _binding = FragmentGuideBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        updateHeaderTime()
        setupRecyclerView()
        setupSearch()
        loadGuide()

        binding.swipeRefreshGuide.setOnRefreshListener {
            loadGuide()
        }
    }

    private fun updateHeaderTime() {
        val sdf = SimpleDateFormat("EEE, MMM d · h:mm a", Locale.getDefault())
        binding.tvGuideTime.text = sdf.format(Date()).uppercase()
    }

    private fun setupRecyclerView() {
        guideAdapter = GuideChannelAdapter { channelRow, entry ->
            showProgramDialog(channelRow, entry)
        }
        binding.rvGuide.layoutManager = LinearLayoutManager(requireContext())
        binding.rvGuide.adapter = guideAdapter
    }

    private fun setupSearch() {
        binding.etGuideSearch.addTextChangedListener(object : TextWatcher {
            override fun afterTextChanged(s: Editable?) {
                val query = s?.toString()?.trim() ?: ""
                filterGuide(query)
            }
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
        })
    }

    private fun loadGuide() {
        binding.swipeRefreshGuide.isRefreshing = true
        lifecycleScope.launch {
            val resp = app.api.getGuide(hours = 4)
            binding.swipeRefreshGuide.isRefreshing = false

            if (resp != null) {
                allGuideRows = resp.guide
                guideAdapter.submitList(allGuideRows)
            }
        }
    }

    private fun filterGuide(query: String) {
        if (query.isBlank()) {
            guideAdapter.submitList(allGuideRows)
            return
        }
        val filtered = allGuideRows.mapNotNull { row ->
            val matchingEntries = row.entries.filter {
                it.title.contains(query, ignoreCase = true) ||
                (it.subtitle?.contains(query, ignoreCase = true) == true) ||
                (it.description?.contains(query, ignoreCase = true) == true)
            }
            if (matchingEntries.isNotEmpty()) {
                row.copy(entries = matchingEntries)
            } else null
        }
        guideAdapter.submitList(filtered)
    }

    private fun showProgramDialog(channelRow: GuideChannelRow, entry: GuideEntry) {
        val channel = channelRow.channel
        val msg = buildString {
            if (!entry.subtitle.isNullOrBlank()) {
                append(entry.subtitle)
                append("\n\n")
            }
            if (!entry.description.isNullOrBlank()) {
                append(entry.description)
                append("\n\n")
            }
            append("Airing: ")
            append(entry.getFormattedAiringTime())
        }

        MaterialAlertDialogBuilder(requireContext())
            .setTitle("${entry.title} (CH ${String.format("%02d", channel.number)})")
            .setMessage(msg)
            .setPositiveButton("TUNE RECEIVER") { _, _ ->
                lifecycleScope.launch {
                    val ok = app.api.tune(channel.number)
                    if (ok) {
                        Toast.makeText(requireContext(), "Tuned receiver to CH ${channel.number}", Toast.LENGTH_SHORT).show()
                    }
                }
            }
            .setNeutralButton("WATCH ON PHONE") { _, _ ->
                (activity as? MainActivity)?.navigateToLiveChannel(channel.number)
            }
            .setNegativeButton("CLOSE", null)
            .show()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }
}
