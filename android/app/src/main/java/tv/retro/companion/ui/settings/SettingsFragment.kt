package tv.retro.companion.ui.settings

import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import tv.retro.companion.RetroTvApp
import tv.retro.companion.databinding.FragmentSettingsBinding
import tv.retro.companion.ui.connect.ConnectActivity

class SettingsFragment : Fragment() {

    private var _binding: FragmentSettingsBinding? = null
    private val binding get() = _binding!!
    private val app get() = RetroTvApp.instance

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?): View {
        _binding = FragmentSettingsBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        val host = app.authManager.serverHost ?: "Not configured"
        val port = app.authManager.serverPort
        binding.tvSettingsHost.text = "$host:$port"

        binding.btnUnpair.setOnClickListener {
            MaterialAlertDialogBuilder(requireContext())
                .setTitle("Unpair Receiver?")
                .setMessage("This device will disconnect from $host:$port. You can re-pair at any time using a new pair code.")
                .setPositiveButton("UNPAIR") { _, _ ->
                    app.authManager.clear()
                    val intent = Intent(requireContext(), ConnectActivity::class.java).apply {
                        flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
                    }
                    startActivity(intent)
                }
                .setNegativeButton("CANCEL", null)
                .show()
        }

        loadSystemHealth()
    }

    private fun loadSystemHealth() {
        lifecycleScope.launch {
            try {
                val url = "${app.authManager.baseUrl}/api/system"
                val json = withContext(Dispatchers.IO) {
                    val client = OkHttpClient()
                    val req = Request.Builder().url(url).build()
                    client.newCall(req).execute().use { resp ->
                        if (resp.isSuccessful) {
                            JSONObject(resp.body?.string() ?: "{}")
                        } else null
                    }
                }

                if (json != null) {
                    val sys = json.optJSONObject("system_health")
                    val temp = sys?.optDouble("cpu_temp_c", 0.0) ?: 0.0
                    val load1 = sys?.optDouble("load_1m", 0.0) ?: 0.0
                    val load5 = sys?.optDouble("load_5m", 0.0) ?: 0.0

                    binding.tvSysCpu.text = "CPU Temp: ${temp}°C"
                    binding.tvSysLoad.text = "Load Average: $load1 (1m), $load5 (5m)"

                    val disk = json.optJSONObject("disk")
                    val free = disk?.optString("free", "--") ?: "--"
                    val total = disk?.optString("total", "--") ?: "--"
                    binding.tvSysDisk.text = "Media Storage: $free free of $total"
                }
            } catch (_: Exception) {}
        }
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }
}
