package com.rid.xorics

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Thin client for the Xorics bridge. Same endpoints the web page and the Termux
 * probe use. All calls are blocking and must be invoked off the main thread.
 */
object Bridge {

    // Bridge exposed via `tailscale serve 8090`. Phone reaches it over the tailnet.
    const val BASE = "https://ridgames.tail893cf4.ts.net"

    // Must match XORICS_BRIDGE_TOKEN on the server if you set one. It is currently
    // unset, so any value is accepted.
    const val TOKEN = "xorics-app"

    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .build()

    private fun auth(b: Request.Builder) = b.addHeader("Authorization", "Bearer $TOKEN")

    /** Send recorded audio bytes, get back the transcript. */
    fun stt(audio: ByteArray): String {
        val req = auth(Request.Builder().url("$BASE/stt"))
            .post(audio.toRequestBody("audio/mp4".toMediaType()))
            .build()
        client.newCall(req).execute().use { r ->
            val body = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw IOException("stt ${r.code}: ${body.take(160)}")
            return JSONObject(body).optString("text").trim()
        }
    }

    /** Send the user's text to the manager, get back Xorics's reply. */
    fun chat(text: String): String {
        val payload = JSONObject()
            .put("model", "xorics")
            .put(
                "messages",
                JSONArray().put(JSONObject().put("role", "user").put("content", text))
            )
        val req = auth(Request.Builder().url("$BASE/v1/chat/completions"))
            .post(payload.toString().toRequestBody("application/json".toMediaType()))
            .build()
        client.newCall(req).execute().use { r ->
            val body = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw IOException("chat ${r.code}: ${body.take(160)}")
            return JSONObject(body)
                .getJSONArray("choices")
                .getJSONObject(0)
                .getJSONObject("message")
                .getString("content")
                .trim()
        }
    }

    /** Send reply text, get back spoken audio (WAV) bytes. */
    fun tts(text: String): ByteArray {
        val payload = JSONObject().put("text", text)
        val req = auth(Request.Builder().url("$BASE/tts"))
            .post(payload.toString().toRequestBody("application/json".toMediaType()))
            .build()
        client.newCall(req).execute().use { r ->
            if (!r.isSuccessful) {
                throw IOException("tts ${r.code}: ${r.body?.string().orEmpty().take(160)}")
            }
            return r.body?.bytes() ?: ByteArray(0)
        }
    }
}
