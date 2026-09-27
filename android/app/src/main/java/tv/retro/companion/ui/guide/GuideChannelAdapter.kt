package tv.retro.companion.ui.guide

import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import tv.retro.companion.data.model.GuideChannelRow
import tv.retro.companion.data.model.GuideEntry
import tv.retro.companion.databinding.ItemGuideChannelBinding

class GuideChannelAdapter(
    private val onProgramClick: (GuideChannelRow, GuideEntry) -> Unit
) : RecyclerView.Adapter<GuideChannelAdapter.GuideChannelViewHolder>() {

    private val items = mutableListOf<GuideChannelRow>()

    fun submitList(channels: List<GuideChannelRow>) {
        items.clear()
        items.addAll(channels)
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): GuideChannelViewHolder {
        val binding = ItemGuideChannelBinding.inflate(LayoutInflater.from(parent.context), parent, false)
        return GuideChannelViewHolder(binding)
    }

    override fun onBindViewHolder(holder: GuideChannelViewHolder, position: Int) {
        holder.bind(items[position])
    }

    override fun getItemCount(): Int = items.size

    inner class GuideChannelViewHolder(private val binding: ItemGuideChannelBinding) : RecyclerView.ViewHolder(binding.root) {
        private val programAdapter = GuideProgramAdapter { entry ->
            val pos = bindingAdapterPosition
            if (pos != RecyclerView.NO_POSITION) {
                onProgramClick(items[pos], entry)
            }
        }

        init {
            binding.rvGuidePrograms.layoutManager =
                LinearLayoutManager(binding.root.context, LinearLayoutManager.HORIZONTAL, false)
            binding.rvGuidePrograms.adapter = programAdapter
        }

        fun bind(row: GuideChannelRow) {
            binding.tvGuideChNum.text = String.format("%02d", row.channel.number)
            binding.tvGuideChName.text = row.channel.name
            programAdapter.submitList(row.entries)
        }
    }
}
