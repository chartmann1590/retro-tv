package tv.retro.companion.ui.main

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.fragment.app.Fragment
import tv.retro.companion.R
import tv.retro.companion.databinding.ActivityMainBinding
import tv.retro.companion.ui.guide.GuideFragment
import tv.retro.companion.ui.live.LiveTvFragment
import tv.retro.companion.ui.remote.RemoteFragment
import tv.retro.companion.ui.settings.SettingsFragment
import tv.retro.companion.ui.vod.VodFragment

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding

    private val liveTvFragment by lazy { LiveTvFragment() }
    private val guideFragment by lazy { GuideFragment() }
    private val vodFragment by lazy { VodFragment() }
    private val remoteFragment by lazy { RemoteFragment() }
    private val settingsFragment by lazy { SettingsFragment() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        if (savedInstanceState == null) {
            switchFragment(liveTvFragment)
        }

        binding.bottomNav.setOnItemSelectedListener { item ->
            when (item.itemId) {
                R.id.nav_live -> {
                    switchFragment(liveTvFragment)
                    true
                }
                R.id.nav_guide -> {
                    switchFragment(guideFragment)
                    true
                }
                R.id.nav_vod -> {
                    switchFragment(vodFragment)
                    true
                }
                R.id.nav_remote -> {
                    switchFragment(remoteFragment)
                    true
                }
                R.id.nav_settings -> {
                    switchFragment(settingsFragment)
                    true
                }
                else -> false
            }
        }
    }

    fun navigateToLiveChannel(channelNumber: Int) {
        binding.bottomNav.selectedItemId = R.id.nav_live
        liveTvFragment.tuneChannel(channelNumber)
    }

    private fun switchFragment(fragment: Fragment) {
        supportFragmentManager.beginTransaction()
            .replace(R.id.fragmentContainer, fragment)
            .commit()
    }
}
