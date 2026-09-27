package tv.retro.companion.ui.live

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.recyclerview.widget.RecyclerView
import tv.retro.companion.data.model.Channel
import tv.retro.companion.databinding.ItemChannelBinding

class ChannelAdapter(
    private val onChannelClick: (Channel) -> Unit
) : RecyclerView.Adapter<ChannelAdapter.ChannelViewHolder>() {

    private val items = mutableListOf<Channel>()
    private var selectedChannel: Int? = null

    fun submitList(channels: List<Channel>, selected: Int? = null) {
        items.clear()
        items.addAll(channels)
        selectedChannel = selected
        notifyDataSetChanged()
    }

    fun setSelected(channelNumber: Int) {
        selectedChannel = channelNumber
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ChannelViewHolder {
        val binding = ItemChannelBinding.inflate(LayoutInflater.from(parent.context), parent, false)
        return ChannelViewHolder(binding)
    }

    override fun onBindViewHolder(holder: ChannelViewHolder, position: Int) {
        holder.bind(items[position])
    }

    override fun getItemCount(): Int = items.size

    inner class ChannelViewHolder(private val binding: ItemChannelBinding) : RecyclerView.ViewHolder(binding.root) {
        fun bind(channel: Channel) {
            binding.tvChNumber.text = String.format("%02d", channel.number)
            binding.tvChName.text = channel.name
            binding.tvFav.visibility = if (channel.favorite == 1) View.VISIBLE else View.GONE

            val isSelected = channel.number == selectedChannel
            binding.root.alpha = if (isSelected) 1.0f else 0.85f

            binding.root.setOnClickListener {
                onChannelClick(channel)
            }
        }
    }
}
