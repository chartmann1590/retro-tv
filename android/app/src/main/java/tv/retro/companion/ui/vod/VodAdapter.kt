package tv.retro.companion.ui.vod

import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.recyclerview.widget.RecyclerView
import coil.load
import tv.retro.companion.RetroTvApp
import tv.retro.companion.data.model.VodMovie
import tv.retro.companion.data.model.VodShow
import tv.retro.companion.databinding.ItemVodCardBinding

sealed class VodItem {
    data class Movie(val movie: VodMovie) : VodItem()
    data class Show(val show: VodShow) : VodItem()
}

class VodAdapter(
    private val onItemClick: (VodItem) -> Unit
) : RecyclerView.Adapter<VodAdapter.VodViewHolder>() {

    private val items = mutableListOf<VodItem>()

    fun submitList(vodItems: List<VodItem>) {
        items.clear()
        items.addAll(vodItems)
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VodViewHolder {
        val binding = ItemVodCardBinding.inflate(LayoutInflater.from(parent.context), parent, false)
        return VodViewHolder(binding)
    }

    override fun onBindViewHolder(holder: VodViewHolder, position: Int) {
        holder.bind(items[position])
    }

    override fun getItemCount(): Int = items.size

    inner class VodViewHolder(private val binding: ItemVodCardBinding) : RecyclerView.ViewHolder(binding.root) {
        fun bind(item: VodItem) {
            val app = RetroTvApp.instance
            when (item) {
                is VodItem.Movie -> {
                    val m = item.movie
                    binding.tvVodTitle.text = m.title
                    binding.tvVodSub.text = buildString {
                        m.year?.let { append(it).append(" · ") }
                        append(m.genre ?: "Movie")
                    }
                    binding.tvVodBadge.text = "MOVIE"

                    val artUrl = app.api.getArtUrl(m.mediaId)
                    binding.ivPoster.load(artUrl) {
                        crossfade(true)
                    }
                }
                is VodItem.Show -> {
                    val s = item.show
                    binding.tvVodTitle.text = s.name
                    binding.tvVodSub.text = "TV Series"
                    binding.tvVodBadge.text = "SERIES"

                    val artUrl = "${app.authManager.baseUrl}/api/art?show=${s.name}"
                    binding.ivPoster.load(artUrl) {
                        crossfade(true)
                    }
                }
            }

            binding.root.setOnClickListener {
                onItemClick(item)
            }
        }
    }
}
