package tv.retro.companion.data.discovery

import android.content.Context
import android.net.wifi.WifiManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress

class DiscoveryManager(private val context: Context) {

    companion object {
        const val DISCOVERY_PORT = 5002
        const val MAGIC_REQUEST = "RETRO_TV_DISCOVER"
    }

    data class DiscoveredServer(
        val host: String,
        val port: Int = 5000,
        val name: String = "Retro TV"
    )

    suspend fun discoverServer(timeoutMs: Int = 2500): DiscoveredServer? = withContext(Dispatchers.IO) {
        // Acquire WiFi MulticastLock if available
        var lock: WifiManager.MulticastLock? = null
        try {
            val wm = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
            lock = wm?.createMulticastLock("RetroTvDiscovery")
            lock?.setReferenceCounted(true)
            lock?.acquire()
        } catch (_: Exception) {}

        var socket: DatagramSocket? = null
        try {
            socket = DatagramSocket()
            socket.broadcast = true
            socket.soTimeout = timeoutMs

            val reqData = MAGIC_REQUEST.toByteArray(Charsets.UTF_8)

            // Try sending to 255.255.255.255
            val broadcastAddr = InetAddress.getByName("255.255.255.255")
            val packet = DatagramPacket(reqData, reqData.size, broadcastAddr, DISCOVERY_PORT)
            socket.send(packet)

            // Listen for response
            val buf = ByteArray(1024)
            val recvPacket = DatagramPacket(buf, buf.size)
            socket.receive(recvPacket)

            val jsonStr = String(recvPacket.data, 0, recvPacket.length, Charsets.UTF_8).trim()
            val json = JSONObject(jsonStr)
            if (json.optString("app") == "retro-tv") {
                val host = recvPacket.address.hostAddress ?: ""
                val port = json.optInt("port", 5000)
                val name = json.optString("name", "Retro TV")
                if (host.isNotBlank()) {
                    return@withContext DiscoveredServer(host, port, name)
                }
            }
        } catch (e: Exception) {
            // UDP broadcast timeout or restricted by router
        } finally {
            try { socket?.close() } catch (_: Exception) {}
            try { lock?.release() } catch (_: Exception) {}
        }

        // Fallback 1: Try mDNS hostname "retro-tv.local"
        try {
            val inet = InetAddress.getByName("retro-tv.local")
            if (inet.isReachable(1000)) {
                return@withContext DiscoveredServer(inet.hostAddress ?: "retro-tv.local", 5000, "Retro TV")
            }
        } catch (_: Exception) {}

        null
    }
}
