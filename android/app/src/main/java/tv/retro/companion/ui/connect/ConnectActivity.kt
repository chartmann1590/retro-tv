package tv.retro.companion.ui.connect

import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import tv.retro.companion.RetroTvApp
import tv.retro.companion.databinding.ActivityConnectBinding
import tv.retro.companion.ui.main.MainActivity

class ConnectActivity : AppCompatActivity() {

    private lateinit var binding: ActivityConnectBinding
    private val app get() = RetroTvApp.instance
    private var detectedHost: String? = null
    private var detectedPort: Int = 5000

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityConnectBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // If already paired, verify and proceed
        if (app.authManager.isPaired) {
            checkExistingPairing()
        } else {
            startDiscovery()
        }

        setupListeners()
    }

    private fun checkExistingPairing() {
        lifecycleScope.launch {
            binding.tvDiscoveryStatus.text = "Connecting to paired receiver…"
            binding.progressScan.visibility = View.VISIBLE

            val valid = app.api.checkPairStatus()
            if (valid) {
                launchMain()
            } else {
                binding.progressScan.visibility = View.GONE
                binding.tvDiscoveryStatus.text = "Pairing expired or receiver unreachable. Re-pairing needed."
                startDiscovery()
            }
        }
    }

    private fun startDiscovery() {
        binding.progressScan.visibility = View.VISIBLE
        binding.dotStatus.visibility = View.GONE
        binding.tvServerDetails.visibility = View.GONE
        binding.tvDiscoveryStatus.text = "Scanning local Wi-Fi for receiver…"

        lifecycleScope.launch {
            val server = app.discovery.discoverServer()
            binding.progressScan.visibility = View.GONE

            if (server != null) {
                // Verify server identity
                val identity = app.api.validateServer(server.host, server.port)
                if (identity != null) {
                    onServerVerified(server.host, server.port, identity.name)
                } else {
                    onDiscoveryFailed("Found device at ${server.host}, but identity verification failed.")
                }
            } else {
                onDiscoveryFailed("Receiver not auto-detected. Enter IP address below.")
            }
        }
    }

    private fun onServerVerified(host: String, port: Int, name: String) {
        detectedHost = host
        detectedPort = port
        binding.dotStatus.visibility = View.VISIBLE
        binding.tvDiscoveryStatus.text = "Receiver Verified: $name"
        binding.tvServerDetails.text = "IP: $host:$port (Online)"
        binding.tvServerDetails.visibility = View.VISIBLE
        binding.tvPairHint.text = "Open http://$host:$port/remote and tap 📱 PAIR APP"
        binding.cardPairing.visibility = View.VISIBLE
    }

    private fun onDiscoveryFailed(message: String) {
        binding.dotStatus.visibility = View.GONE
        binding.tvDiscoveryStatus.text = message
        binding.layoutManual.visibility = View.VISIBLE
    }

    private fun setupListeners() {
        binding.btnRescan.setOnClickListener {
            startDiscovery()
        }

        binding.btnToggleManual.setOnClickListener {
            binding.layoutManual.visibility =
                if (binding.layoutManual.visibility == View.VISIBLE) View.GONE else View.VISIBLE
        }

        binding.btnValidateIp.setOnClickListener {
            val input = binding.etIp.text?.toString()?.trim() ?: ""
            if (input.isBlank()) {
                binding.tilIp.error = "Enter an IP address"
                return@setOnClickListener
            }
            binding.tilIp.error = null

            val parts = input.removePrefix("http://").removePrefix("https://").split(":")
            val host = parts[0]
            val port = if (parts.size > 1) parts[1].toIntOrNull() ?: 5000 else 5000

            lifecycleScope.launch {
                binding.btnValidateIp.isEnabled = false
                binding.btnValidateIp.text = "VERIFYING…"
                val identity = app.api.validateServer(host, port)
                binding.btnValidateIp.isEnabled = true
                binding.btnValidateIp.text = "VALIDATE & CONNECT"

                if (identity != null) {
                    onServerVerified(host, port, identity.name)
                    Toast.makeText(this@ConnectActivity, "Verified Retro TV Server!", Toast.LENGTH_SHORT).show()
                } else {
                    Toast.makeText(this@ConnectActivity, "Could not verify Retro TV at $host:$port", Toast.LENGTH_LONG).show()
                }
            }
        }

        binding.btnPair.setOnClickListener {
            val code = binding.etPairCode.text?.toString()?.trim() ?: ""
            if (code.length != 6) {
                binding.tvPairError.text = "Please enter the 6-digit code"
                binding.tvPairError.visibility = View.VISIBLE
                return@setOnClickListener
            }
            binding.tvPairError.visibility = View.GONE

            val host = detectedHost
            if (host.isNullOrBlank()) {
                binding.tvPairError.text = "No server connected yet"
                binding.tvPairError.visibility = View.VISIBLE
                return@setOnClickListener
            }

            val deviceName = "${Build.MANUFACTURER} ${Build.MODEL}"
            lifecycleScope.launch {
                binding.btnPair.isEnabled = false
                binding.btnPair.text = "PAIRING…"

                val resp = app.api.pairDevice(host, detectedPort, code, deviceName)
                binding.btnPair.isEnabled = true
                binding.btnPair.text = "PAIR RECEIVER"

                if (resp.ok && !resp.token.isNullOrBlank()) {
                    app.authManager.savePairing(
                        host = host,
                        port = detectedPort,
                        token = resp.token,
                        name = resp.serverName ?: "Retro TV"
                    )
                    Toast.makeText(this@ConnectActivity, "Paired Successfully!", Toast.LENGTH_SHORT).show()
                    launchMain()
                } else {
                    binding.tvPairError.text = resp.error ?: "Invalid or expired code"
                    binding.tvPairError.visibility = View.VISIBLE
                }
            }
        }
    }

    private fun launchMain() {
        startActivity(Intent(this, MainActivity::class.java))
        finish()
    }
}
