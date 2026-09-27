package tv.retro.companion.data.model

import com.google.gson.annotations.SerializedName
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

data class ServerIdentity(
    val app: String = "",
    val name: String = "",
    val version: String = "",
    val port: Int = 5000,
    @SerializedName("paired_required") val pairedRequired: Boolean = true,
    @SerializedName("server_time") val serverTime: Double? = null,
    val timezone: String? = null
)

data class PairRequest(
    val code: String,
    @SerializedName("device_name") val deviceName: String
)

data class PairResponse(
    val ok: Boolean = false,
    val token: String? = null,
    @SerializedName("server_name") val serverName: String? = null,
    val message: String? = null,
    val error: String? = null
)

data class PairStatusResponse(
    val ok: Boolean = false,
    val paired: Boolean = false
)

data class Channel(
    val number: Int,
    val name: String,
    val enabled: Int = 1,
    val color: String? = "#1a3a6b",
    val logo: String? = "",
    val ordering: String? = "shuffle",
    val favorite: Int = 0,
    @SerializedName("sort_order") val sortOrder: Int = 0
)

data class ChannelsResponse(
    val ok: Boolean = false,
    val channels: List<Channel> = emptyList()
)

data class NowPlayingEntry(
    val id: Int? = null,
    val title: String = "Off Air",
    val subtitle: String? = "",
    val description: String? = "",
    val duration: Double? = 0.0,
    @SerializedName("start_ts") val startTs: Double? = 0.0,
    @SerializedName("end_ts") val endTs: Double? = 0.0,
    val kind: String? = "episode",
    @SerializedName("media_id") val mediaId: Int? = null,
    val artwork: String? = ""
)

data class HdmiStatus(
    val ok: Boolean = false,
    val channel: Int? = null,
    @SerializedName("prev_channel") val prevChannel: Int? = null,
    @SerializedName("is_vod") val isVod: Boolean = false,
    val volume: Any? = 80,
    val muted: Any? = false,
    val paused: Boolean = false,
    @SerializedName("mpv_alive") val mpvAlive: Boolean = true,
    val entry: NowPlayingEntry? = null,
    @SerializedName("vod_info") val vodInfo: Map<String, Any?>? = null
) {
    fun getVolumeInt(): Int {
        return when (volume) {
            is Number -> volume.toInt()
            is String -> volume.toIntOrNull() ?: 80
            else -> 80
        }
    }

    fun isMuted(): Boolean {
        return when (muted) {
            is Boolean -> muted
            is Number -> muted.toInt() == 1
            is String -> muted == "1" || muted.equals("true", ignoreCase = true)
            else -> false
        }
    }
}

data class GuideEntry(
    val id: Int = 0,
    val title: String = "",
    val subtitle: String? = "",
    val description: String? = "",
    @SerializedName("start_ts") val startTs: Double = 0.0,
    @SerializedName("end_ts") val endTs: Double = 0.0,
    val duration: Double? = 0.0,
    @SerializedName("start_fmt") val startFmt: String? = "",
    @SerializedName("end_fmt") val endFmt: String? = ""
) {
    fun getFormattedStartTime(): String {
        if (!startFmt.isNullOrBlank()) return startFmt
        if (startTs <= 0.0) return ""
        val sdf = SimpleDateFormat("h:mm a", Locale.getDefault())
        return sdf.format(Date((startTs * 1000).toLong()))
    }

    fun getFormattedAiringTime(): String {
        val start = getFormattedStartTime()
        val end = if (!endFmt.isNullOrBlank()) {
            endFmt
        } else if (endTs > startTs) {
            val sdf = SimpleDateFormat("h:mm a", Locale.getDefault())
            sdf.format(Date((endTs * 1000).toLong()))
        } else {
            ""
        }
        return if (end.isNotBlank()) "$start - $end" else start
    }
}

data class GuideChannelRow(
    val channel: Channel,
    val entries: List<GuideEntry> = emptyList()
)

data class GuideResponse(
    val guide: List<GuideChannelRow> = emptyList()
)

data class VodMovie(
    val id: Int,
    @SerializedName("media_id") val mediaId: Int,
    val title: String,
    val year: Int? = null,
    val description: String? = "",
    val runtime: Double? = 0.0,
    val artwork: String? = "",
    val genre: String? = ""
)

data class VodEpisode(
    val id: Int,
    @SerializedName("media_id") val mediaId: Int,
    @SerializedName("show_id") val showId: Int,
    @SerializedName("show_name") val showName: String? = "",
    val season: Int? = 1,
    val episode: Int? = 1,
    val title: String? = "",
    val description: String? = "",
    val runtime: Double? = 0.0,
    val artwork: String? = ""
)

data class VodSeason(
    val season: Int = 1,
    val episodes: List<VodEpisode> = emptyList()
)

data class VodShow(
    val id: Int,
    val name: String,
    val title: String? = "",
    val poster: String? = "",
    val artwork: String? = "",
    val description: String? = "",
    @SerializedName("episode_count") val episode_count: Int? = 0,
    @SerializedName("season_count") val season_count: Int? = 0,
    val seasons: List<VodSeason>? = null
)

data class VodCategory(
    val id: String = "",
    val title: String = "",
    val badge: String? = "",
    val type: String? = ""
)

data class VodCatalogResponse(
    val ok: Boolean? = true,
    @SerializedName("total_movies") val totalMovies: Int? = 0,
    @SerializedName("total_shows") val totalShows: Int? = 0,
    val movies: List<VodMovie> = emptyList(),
    val shows: List<VodShow> = emptyList(),
    val categories: List<VodCategory> = emptyList()
)

data class VodShowResponse(
    val ok: Boolean = true,
    val show: VodShow? = null,
    val error: String? = null
)

data class NowPlayingResponse(
    val ok: Boolean = false,
    val entry: GuideEntry? = null,
    val offset: Double = 0.0,
    val duration: Double = 0.0,
    val range: String? = "",
    @SerializedName("has_media") val hasMedia: Boolean = false,
    @SerializedName("server_time") val serverTime: Double = 0.0,
    @SerializedName("media_key") val mediaKey: String? = null,
    val error: String? = null
)
