package tv.retro.companion

import android.app.Application
import tv.retro.companion.data.api.RetroTvApi
import tv.retro.companion.data.auth.AuthManager
import tv.retro.companion.data.discovery.DiscoveryManager

class RetroTvApp : Application() {

    lateinit var authManager: AuthManager
        private set

    lateinit var api: RetroTvApi
        private set

    lateinit var discovery: DiscoveryManager
        private set

    override fun onCreate() {
        super.onCreate()
        instance = this
        authManager = AuthManager(this)
        api = RetroTvApi(authManager)
        discovery = DiscoveryManager(this)
    }

    companion object {
        lateinit var instance: RetroTvApp
            private set
    }
}
