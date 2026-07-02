package com.rid.xorics.g2

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.graphics.Typeface
import android.os.Build
import android.os.Bundle
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import com.rid.xorics.Bridge
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

/**
 * GlassesActivity — the base system's test bench. Registered as a SECOND
 * launcher icon ("Xorics G2") so it needs zero edits to the existing
 * activities; delete the manifest entry once the G2 path is wired into the
 * main UI.
 *
 * Buttons in test order:
 *   1  offline oracles (no perms, no BLE, no network)
 *   2  runtime BLE perms → start the foreground service (needs glasses on and
 *      Even Hub UNINSTALLED — it hogs the peripheral and causes status 133)
 *   3  POST display_text to the bridge bus — exercises the FULL plugin loop:
 *      bridge queue → phone long-poll → session → BLE → lens
 *   4  stop the service
 *
 * Plain programmatic views (no Compose, no XML resources) to keep the
 * footprint at exactly one file + one manifest entry.
 */
class GlassesActivity : Activity() {

    private lateinit var logView: TextView
    private val http = OkHttpClient()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val pad = (16 * resources.displayMetrics.density).toInt()
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
        }
        root.addView(TextView(this).apply { text = "Xorics G2 — base system v0"; textSize = 18f })

        fun button(label: String, onClick: () -> Unit) {
            root.addView(Button(this).apply { text = label; setOnClickListener { onClick() } },
                LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        }
        button("1 · Self-test (offline oracles)") { runSelfTest() }
        button("2 · Start glasses service (LEFT)") { ensurePermsThenStart() }
        button("3 · Send test text via bus") { sendTestText() }
        button("4 · Stop service") { GlassesService.stop(this); log("service stop requested") }

        logView = TextView(this).apply { textSize = 12f; typeface = Typeface.MONOSPACE }
        root.addView(ScrollView(this).apply { addView(logView) },
            LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        setContentView(root)

        log("order: 1 anywhere · 2 needs glasses on + Even Hub uninstalled · 3 needs bridge reachable")
        log("watch: logcat -s XoricsG2   and   curl {bridge}/glasses/events on RIDGames")
    }

    private fun runSelfTest() {
        log("GlassesProtocol.selfTest() = " + GlassesProtocol.selfTest())
        log("GlassesText.selfTest()     = " + GlassesText.selfTest())
    }

    private fun blePerms(): Array<String> =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S)
            arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
        else
            arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)

    private fun ensurePermsThenStart() {
        val missing = blePerms().filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
        if (missing.isEmpty()) {
            GlassesService.start(this, "LEFT")
            log("service starting — LEFT arm. Watch the notification + logcat.")
        } else {
            log("requesting: ${missing.joinToString()}")
            requestPermissions(missing.toTypedArray(), REQ_BLE)
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != REQ_BLE) return
        if (grantResults.isNotEmpty() && grantResults.all { it == PackageManager.PERMISSION_GRANTED }) {
            GlassesService.start(this, "LEFT")
            log("perms granted — service starting")
        } else {
            val denied = permissions.filterIndexed { i, _ -> grantResults.getOrNull(i) != PackageManager.PERMISSION_GRANTED }
            log("perms denied: $denied — service not started")
        }
    }

    /** Full plugin-path exercise from the phone itself: same POST a Xorics plugin makes. */
    private fun sendTestText() {
        log("POST ${Bridge.BASE}/glasses/command …")
        Thread {
            try {
                val payload = JSONObject()
                    .put("type", "display_text")
                    .put("text", "Hello from Xorics!\nBase system online.")
                val req = Request.Builder()
                    .url("${Bridge.BASE}/glasses/command")
                    .addHeader("Authorization", "Bearer ${Bridge.TOKEN}")
                    .post(payload.toString().toRequestBody("application/json".toMediaType()))
                    .build()
                http.newCall(req).execute().use { r ->
                    log("bridge: HTTP ${r.code} ${r.body?.string().orEmpty().take(120)}")
                    log("if the service is polling, the text plays within ~2s of the next poll cycle")
                }
            } catch (e: Exception) {
                log("bridge unreachable: ${e.message}")
            }
        }.start()
    }

    private fun log(s: String) = runOnUiThread { logView.append(s + "\n") }

    private companion object { const val REQ_BLE = 42 }
}
