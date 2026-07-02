package com.rid.xorics.g2

import android.os.Handler
import android.os.Looper
import com.rid.xorics.Bridge
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * GlassesBridgeClient — the phone half of the plugin bus.
 *
 * Long-polls GET  {Bridge.BASE}/glasses/poll   for commands from Xorics plugins,
 * pushes to POST {Bridge.BASE}/glasses/event   acks, lifecycle notes, and every
 * BLE frame — so Milestone-1 evidence is curl-able on RIDGames without adb.
 *
 * Reuses the app's existing Bridge BASE/TOKEN (tailnet + optional bearer), same
 * blocking-OkHttp-off-main style as Bridge.kt. Commands are dispatched onto the
 * main thread because GlassesSession is Handler/main-thread based.
 *
 * v0 commands: display_text {text}, selftest, ping.
 */
class GlassesBridgeClient(
    private val session: GlassesSession,
    private val log: (String) -> Unit,
) {

    private val http = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(45, TimeUnit.SECONDS)   // > poll wait
        .build()

    private val main = Handler(Looper.getMainLooper())
    private val sender = Executors.newSingleThreadExecutor { r -> Thread(r, "g2-bus-send").apply { isDaemon = true } }
    @Volatile private var running = false
    private var pollThread: Thread? = null

    fun start() {
        if (running) return
        running = true
        pollThread = Thread({ pollLoop() }, "g2-bus-poll").apply { isDaemon = true; start() }
        postEvent(JSONObject().put("type", "bus").put("detail", "phone bus client started, arm=${session.arm}"))
    }

    fun stop() {
        running = false
        pollThread?.interrupt()
        sender.shutdown()
    }

    /** Wire this from the session callback so every notification reaches RIDGames. */
    fun reportFrame(frame: GlassesProtocol.Frame?, raw: ByteArray) {
        val e = JSONObject().put("type", "frame").put("hex", GlassesProtocol.toHex(raw))
        if (frame != null) {
            e.put("frame_type", String.format("%02x", frame.type))
                .put("service", String.format("%02x%02x", frame.serviceHi, frame.serviceLo))
                .put("seq", frame.seq)
        } else {
            e.put("parsed", false)
        }
        postEvent(e)
    }

    fun reportNote(kind: String, detail: String) =
        postEvent(JSONObject().put("type", kind).put("detail", detail))

    // ── internals ────────────────────────────────────────────────────────────

    private fun pollLoop() {
        log("bus: polling ${Bridge.BASE}/glasses/poll")
        while (running) {
            try {
                val req = Request.Builder()
                    .url("${Bridge.BASE}/glasses/poll?wait=30")
                    .addHeader("Authorization", "Bearer ${Bridge.TOKEN}")
                    .get().build()
                http.newCall(req).execute().use { r ->
                    when {
                        r.code == 204 -> Unit // quiet long-poll timeout; loop again
                        r.isSuccessful -> {
                            val body = r.body?.string().orEmpty()
                            val cmd = JSONObject(body).optJSONObject("command")
                            if (cmd != null) dispatch(cmd)
                        }
                        else -> { log("bus: poll HTTP ${r.code}"); sleepQuiet(3000) }
                    }
                }
            } catch (e: InterruptedException) {
                return
            } catch (e: Exception) {
                if (!running) return
                log("bus: poll error ${e.message} — retrying in 3s")
                sleepQuiet(3000)
            }
        }
    }

    private fun dispatch(cmd: JSONObject) {
        val id = cmd.optInt("id", -1)
        val type = cmd.optString("type")
        log("bus: command #$id $type")
        when (type) {
            "display_text" -> {
                val text = cmd.optString("text", "")
                if (text.isEmpty()) { ack(id, false, "empty text"); return }
                main.post {
                    val immediate = session.showText(text)
                    ack(id, true, if (immediate) "playing" else "queued (state=${session.state})")
                }
            }
            "selftest" -> {
                val proto = GlassesProtocol.selfTest()
                val text = GlassesText.selfTest()
                ack(id, proto == "OK" && text == "OK", "protocol=$proto text=$text")
            }
            "ping" -> ack(id, true, "pong state=${session.state} frames=${session.recentFramesHex().size}")
            else -> ack(id, false, "unknown command type '$type'")
        }
    }

    private fun ack(cmdId: Int, ok: Boolean, detail: String) =
        postEvent(JSONObject().put("type", "ack").put("cmd_id", cmdId).put("ok", ok).put("detail", detail))

    private fun postEvent(payload: JSONObject) {
        sender.execute {
            try {
                val req = Request.Builder()
                    .url("${Bridge.BASE}/glasses/event")
                    .addHeader("Authorization", "Bearer ${Bridge.TOKEN}")
                    .post(payload.toString().toRequestBody("application/json".toMediaType()))
                    .build()
                http.newCall(req).execute().use { }
            } catch (e: Exception) {
                log("bus: event post failed ${e.message}")
            }
        }
    }

    private fun sleepQuiet(ms: Long) { try { Thread.sleep(ms) } catch (_: InterruptedException) {} }
}
