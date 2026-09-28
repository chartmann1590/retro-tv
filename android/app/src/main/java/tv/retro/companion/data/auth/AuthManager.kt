package tv.retro.companion.data.auth

import android.content.Context
import android.content.SharedPreferences

class AuthManager(context: Context) {
    private val prefs: SharedPreferences = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    companion object {
        private const val PREFS_NAME = "retro_tv_prefs"
        private const val KEY_SERVER_HOST = "server_host"
        private const val KEY_SERVER_PORT = "server_port"
        private const val KEY_DEVICE_TOKEN = "device_token"
        private const val KEY_SERVER_NAME = "server_name"
    }

    var serverHost: String?
        get() = prefs.getString(KEY_SERVER_HOST, null)
        set(value) = prefs.edit().putString(KEY_SERVER_HOST, value).apply()

    var serverPort: Int
        get() = prefs.getInt(KEY_SERVER_PORT, 5000)
        set(value) = prefs.edit().putInt(KEY_SERVER_PORT, value).apply()

    var deviceToken: String?
        get() = prefs.getString(KEY_DEVICE_TOKEN, null)
        set(value) = prefs.edit().putString(KEY_DEVICE_TOKEN, value).apply()

    var serverName: String
        get() = prefs.getString(KEY_SERVER_NAME, "Retro TV") ?: "Retro TV"
        set(value) = prefs.edit().putString(KEY_SERVER_NAME, value).apply()

    val isPaired: Boolean
        get() = !serverHost.isNullOrBlank() && !deviceToken.isNullOrBlank()

    val baseUrl: String
        get() {
            val host = serverHost ?: "127.0.0.1"
            return "http://$host:$serverPort"
        }

    fun savePairing(host: String, port: Int, token: String, name: String = "Retro TV") {
        prefs.edit()
            .putString(KEY_SERVER_HOST, host)
            .putInt(KEY_SERVER_PORT, port)
            .putString(KEY_DEVICE_TOKEN, token)
            .putString(KEY_SERVER_NAME, name)
            .apply()
    }

    fun clear() {
        prefs.edit().clear().apply()
    }
}
