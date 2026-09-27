package tv.retro.companion.data.api

import com.google.gson.Gson
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import tv.retro.companion.data.auth.AuthManager
import tv.retro.companion.data.model.*
import java.util.concurrent.TimeUnit

class RetroTvApi(private val authManager: AuthManager) {

    private val gson = Gson()
    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .writeTimeout(5, TimeUnit.SECONDS)
        .build()

    private val jsonMediaType = "application/json; charset=utf-8".toMediaType()

    suspend fun validateServer(host: String, port: Int = 5000): ServerIdentity? = withContext(Dispatchers.IO) {
        try {
            val url = "http://$host:$port/api/server/identity"
            val request = Request.Builder().url(url).build()
            client.newCall(request).execute().use { resp ->
                if (resp.isSuccessful) {
                    val body = resp.body?.string() ?: return@withContext null
                    val identity = gson.fromJson(body, ServerIdentity::class.java)
                    if (identity.app == "retro-tv") return@withContext identity
                }
            }
        } catch (_: Exception) {}
        null
    }

    suspend fun pairDevice(host: String, port: Int, code: String, deviceName: String): PairResponse = withContext(Dispatchers.IO) {
        try {
            val url = "http://$host:$port/api/pair/verify"
            val payload = gson.toJson(PairRequest(code = code, deviceName = deviceName))
            val request = Request.Builder()
                .url(url)
                .post(payload.toRequestBody(jsonMediaType))
                .build()
            client.newCall(request).execute().use { resp ->
                val body = resp.body?.string() ?: ""
                return@withContext gson.fromJson(body, PairResponse::class.java)
            }
        } catch (e: Exception) {
            PairResponse(ok = false, error = e.localizedMessage ?: "Connection error")
        }
    }

    suspend fun checkPairStatus(): Boolean = withContext(Dispatchers.IO) {
        try {
            val url = "${authManager.baseUrl}/api/pair/status"
            val request = authRequest(url).build()
            client.newCall(request).execute().use { resp ->
                if (resp.isSuccessful) {
                    val res = gson.fromJson(resp.body?.string(), PairStatusResponse::class.java)
                    return@withContext res.ok
                }
            }
        } catch (_: Exception) {}
        false
    }

    suspend fun getHdmiStatus(): HdmiStatus? = withContext(Dispatchers.IO) {
        try {
            val url = "${authManager.baseUrl}/api/hdmi"
            val request = authRequest(url).build()
            client.newCall(request).execute().use { resp ->
                if (resp.isSuccessful) {
                    return@withContext gson.fromJson(resp.body?.string(), HdmiStatus::class.java)
                }
            }
        } catch (_: Exception) {}
        null
    }

    suspend fun getChannels(): List<Channel> = withContext(Dispatchers.IO) {
        try {
            val url = "${authManager.baseUrl}/api/channels"
            val request = authRequest(url).build()
            client.newCall(request).execute().use { resp ->
                if (resp.isSuccessful) {
                    val res = gson.fromJson(resp.body?.string(), ChannelsResponse::class.java)
                    return@withContext res.channels.filter { it.enabled == 1 }
                }
            }
        } catch (_: Exception) {}
        emptyList()
    }

    suspend fun tune(channel: Int): Boolean = withContext(Dispatchers.IO) {
        try {
            val url = "${authManager.baseUrl}/api/tune"
            val payload = gson.toJson(mapOf("channel" to channel))
            val request = authRequest(url).post(payload.toRequestBody(jsonMediaType)).build()
            client.newCall(request).execute().use { resp ->
                return@withContext resp.isSuccessful
            }
        } catch (_: Exception) {
            false
        }
    }

    suspend fun setVolume(volume: Int? = null, muted: Boolean? = null, togglePause: Boolean? = null): HdmiStatus? = withContext(Dispatchers.IO) {
        try {
            val url = "${authManager.baseUrl}/api/volume"
            val map = mutableMapOf<String, Any>()
            volume?.let { map["volume"] = it }
            muted?.let { map["muted"] = it }
            togglePause?.let { map["toggle_pause"] = it }
            val payload = gson.toJson(map)
            val request = authRequest(url).post(payload.toRequestBody(jsonMediaType)).build()
            client.newCall(request).execute().use { resp ->
                if (resp.isSuccessful) {
                    return@withContext gson.fromJson(resp.body?.string(), HdmiStatus::class.java)
                }
            }
        } catch (_: Exception) {}
        null
    }

    suspend fun setCaptions(enabled: Boolean): Boolean = withContext(Dispatchers.IO) {
        try {
            val url = "${authManager.baseUrl}/api/captions"
            val payload = gson.toJson(mapOf("enabled" to enabled))
            val request = authRequest(url).post(payload.toRequestBody(jsonMediaType)).build()
            client.newCall(request).execute().use { resp ->
                return@withContext resp.isSuccessful
            }
        } catch (_: Exception) {
            false
        }
    }

    suspend fun getGuide(hours: Int = 4): GuideResponse? = withContext(Dispatchers.IO) {
        try {
            val url = "${authManager.baseUrl}/api/guide?hours=$hours"
            val request = authRequest(url).build()
            client.newCall(request).execute().use { resp ->
                if (resp.isSuccessful) {
                    return@withContext gson.fromJson(resp.body?.string(), GuideResponse::class.java)
                }
            }
        } catch (_: Exception) {}
        null
    }

    suspend fun getVodCatalog(): VodCatalogResponse? = withContext(Dispatchers.IO) {
        try {
            val url = "${authManager.baseUrl}/api/vod/catalog"
            val request = authRequest(url).build()
            client.newCall(request).execute().use { resp ->
                if (resp.isSuccessful) {
                    return@withContext gson.fromJson(resp.body?.string(), VodCatalogResponse::class.java)
                }
            }
        } catch (_: Exception) {}
        null
    }

    suspend fun getVodShow(showId: Int): VodShow? = withContext(Dispatchers.IO) {
        try {
            val url = "${authManager.baseUrl}/api/vod/show/$showId"
            val request = authRequest(url).build()
            client.newCall(request).execute().use { resp ->
                if (resp.isSuccessful) {
                    return@withContext gson.fromJson(resp.body?.string(), VodShow::class.java)
                }
            }
        } catch (_: Exception) {}
        null
    }

    suspend fun playVod(mediaId: Int): Boolean = withContext(Dispatchers.IO) {
        try {
            val url = "${authManager.baseUrl}/api/vod/play"
            val payload = gson.toJson(mapOf("media_id" to mediaId))
            val request = authRequest(url).post(payload.toRequestBody(jsonMediaType)).build()
            client.newCall(request).execute().use { resp ->
                return@withContext resp.isSuccessful
            }
        } catch (_: Exception) {
            false
        }
    }

    suspend fun stopVod(): Boolean = withContext(Dispatchers.IO) {
        try {
            val url = "${authManager.baseUrl}/api/vod/stop"
            val payload = "{}"
            val request = authRequest(url).post(payload.toRequestBody(jsonMediaType)).build()
            client.newCall(request).execute().use { resp ->
                return@withContext resp.isSuccessful
            }
        } catch (_: Exception) {
            false
        }
    }

    suspend fun sendTvGuideNav(action: String): Boolean = withContext(Dispatchers.IO) {
        try {
            val url = "${authManager.baseUrl}/api/tv-guide/nav"
            val payload = gson.toJson(mapOf("action" to action))
            val request = authRequest(url).post(payload.toRequestBody(jsonMediaType)).build()
            client.newCall(request).execute().use { resp ->
                return@withContext resp.isSuccessful
            }
        } catch (_: Exception) {
            false
        }
    }

    suspend fun openTvGuide(): Boolean = withContext(Dispatchers.IO) {
        try {
            val url = "${authManager.baseUrl}/api/tv-guide"
            val payload = "{}"
            val request = authRequest(url).post(payload.toRequestBody(jsonMediaType)).build()
            client.newCall(request).execute().use { resp ->
                return@withContext resp.isSuccessful
            }
        } catch (_: Exception) {
            false
        }
    }

    suspend fun openTvVod(): Boolean = withContext(Dispatchers.IO) {
        try {
            val url = "${authManager.baseUrl}/api/tv-vod"
            val payload = "{}"
            val request = authRequest(url).post(payload.toRequestBody(jsonMediaType)).build()
            client.newCall(request).execute().use { resp ->
                return@withContext resp.isSuccessful
            }
        } catch (_: Exception) {
            false
        }
    }

    suspend fun showInfo(): Boolean = withContext(Dispatchers.IO) {
        try {
            val url = "${authManager.baseUrl}/api/info"
            val payload = "{}"
            val request = authRequest(url).post(payload.toRequestBody(jsonMediaType)).build()
            client.newCall(request).execute().use { resp ->
                return@withContext resp.isSuccessful
            }
        } catch (_: Exception) {
            false
        }
    }

    fun getLiveStreamUrl(channelNumber: Int): String {
        return "${authManager.baseUrl}/stream/live/$channelNumber"
    }

    fun getMediaFileStreamUrl(mediaId: Int): String {
        return "${authManager.baseUrl}/stream/file/$mediaId"
    }

    fun getArtUrl(mediaId: Int): String {
        return "${authManager.baseUrl}/api/vod/art?media_id=$mediaId"
    }

    private fun authRequest(url: String): Request.Builder {
        val b = Request.Builder().url(url)
        authManager.deviceToken?.let {
            b.addHeader("Authorization", "Bearer $it")
            b.addHeader("X-Device-Token", it)
        }
        return b
    }
}
