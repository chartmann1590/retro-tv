package tv.retro.companion.ui.guide

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.recyclerview.widget.RecyclerView
import tv.retro.companion.data.model.GuideEntry
import tv.retro.companion.databinding.ItemGuideProgramBinding

class GuideProgramAdapter(
    private val onProgramClick: (GuideEntry) -> Unit
) : RecyclerView.Adapter<GuideProgramAdapter.ProgramViewHolder>() {

    private val items = mutableListOf<GuideEntry>()

    fun submitList(programs: List<GuideEntry>) {
        items.clear()
        items.addAll(programs)
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ProgramViewHolder {
        val binding = ItemGuideProgramBinding.inflate(LayoutInflater.from(parent.context), parent, false)
        return ProgramViewHolder(binding)
    }

    override fun onBindViewHolder(holder: ProgramViewHolder, position: Int) {
        holder.bind(items[position])
    }

    override fun getItemCount(): Int = items.size

    inner class ProgramViewHolder(private val binding: ItemGuideProgramBinding) : RecyclerView.ViewHolder(binding.root) {
        fun bind(entry: GuideEntry) {
            binding.tvProgTitle.text = entry.title
            binding.tvProgSub.text = entry.subtitle ?: ""
            binding.tvProgramTime.text = entry.getFormattedStartTime()

            val now = System.currentTimeMillis() / 1000.0
            val isLive = now >= entry.startTs && now < entry.endTs
            binding.badgeLive.visibility = if (isLive) View.VISIBLE else View.GONE

            binding.root.setOnClickListener {
                onProgramClick(entry)
            }
        }
    }
}
