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

    /** One turn in a conversation. */
    data class Msg(val role: String, val content: String)

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

    /**
     * Send the user's text to the manager, get back Xorics's reply.
     * NOTE: this hits the STATELESS OpenAI route — single-shot, no memory. The voice
     * round-trip still uses it. The chat screen uses the memory route below instead.
     */
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

    // ===================== memory API (the chat screen) =====================
    // These talk to the persisted, history-aware routes in api.py.

    /** Create a new (loose) chat; returns its id. */
    fun createChat(): String {
        val req = auth(Request.Builder().url("$BASE/v1/chats"))
            .post("{}".toRequestBody("application/json".toMediaType()))
            .build()
        client.newCall(req).execute().use { r ->
            val body = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw IOException("createChat ${r.code}: ${body.take(160)}")
            return JSONObject(body).getString("id")
        }
    }

    /** Load a chat's full message history, oldest first. */
    fun getMessages(chatId: String): List<Msg> {
        val req = auth(Request.Builder().url("$BASE/v1/chats/$chatId/messages")).get().build()
        client.newCall(req).execute().use { r ->
            val body = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw IOException("getMessages ${r.code}: ${body.take(160)}")
            val arr = JSONObject(body).getJSONArray("messages")
            val out = ArrayList<Msg>(arr.length())
            for (i in 0 until arr.length()) {
                val m = arr.getJSONObject(i)
                out.add(Msg(m.getString("role"), m.getString("content")))
            }
            return out
        }
    }

    /** Send a user turn to a chat (server feeds prior turns back to the model); returns the reply. */
    fun sendMessage(chatId: String, content: String): Msg {
        val payload = JSONObject().put("content", content)
        val req = auth(Request.Builder().url("$BASE/v1/chats/$chatId/messages"))
            .post(payload.toString().toRequestBody("application/json".toMediaType()))
            .build()
        client.newCall(req).execute().use { r ->
            val body = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw IOException("sendMessage ${r.code}: ${body.take(160)}")
            val a = JSONObject(body).getJSONObject("assistant_message")
            return Msg(a.getString("role"), a.getString("content"))
        }
    }

    // ===================== files API (the file explorer) ====================
    data class FileItem(val id: String, val name: String, val folder: String, val size: Long, val mime: String)

    private fun parseFile(o: JSONObject) = FileItem(
        o.getString("id"), o.getString("name"), o.optString("folder", "/"),
        o.optLong("size", 0), o.optString("mime", "")
    )

    /** List stored files. folder=null lists everything; otherwise just that folder. */
    fun listFiles(folder: String? = null): List<FileItem> {
        val url = if (folder == null) "$BASE/v1/files"
        else "$BASE/v1/files?folder=" + java.net.URLEncoder.encode(folder, "UTF-8")
        val req = auth(Request.Builder().url(url)).get().build()
        client.newCall(req).execute().use { r ->
            val body = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw IOException("listFiles ${r.code}: ${body.take(160)}")
            val arr = JSONObject(body).getJSONArray("files")
            val out = ArrayList<FileItem>(arr.length())
            for (i in 0 until arr.length()) out.add(parseFile(arr.getJSONObject(i)))
            return out
        }
    }

    /** Distinct folder paths that contain files — the dir set the explorer renders. */
    fun listFolders(): List<String> {
        val req = auth(Request.Builder().url("$BASE/v1/folders")).get().build()
        client.newCall(req).execute().use { r ->
            val body = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw IOException("listFolders ${r.code}: ${body.take(160)}")
            val arr = JSONObject(body).getJSONArray("folders")
            val out = ArrayList<String>(arr.length())
            for (i in 0 until arr.length()) out.add(arr.getString(i))
            return out
        }
    }

    /** Upload bytes (base64-encoded here) into a folder; returns the stored file. */
    fun uploadFile(name: String, bytes: ByteArray, folder: String): FileItem {
        val b64 = android.util.Base64.encodeToString(bytes, android.util.Base64.NO_WRAP)
        val payload = JSONObject().put("filename", name).put("data", b64).put("folder", folder)
        val req = auth(Request.Builder().url("$BASE/v1/files"))
            .post(payload.toString().toRequestBody("application/json".toMediaType()))
            .build()
        client.newCall(req).execute().use { r ->
            val body = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw IOException("uploadFile ${r.code}: ${body.take(160)}")
            return parseFile(JSONObject(body))
        }
    }

    /** Delete a stored file (removes the row and its bytes). */
    fun deleteFile(id: String) {
        val req = auth(Request.Builder().url("$BASE/v1/files/$id")).delete().build()
        client.newCall(req).execute().use { r ->
            if (!r.isSuccessful) throw IOException("deleteFile ${r.code}: ${r.body?.string().orEmpty().take(160)}")
        }
    }

    /** Move a file to another folder; the server relocates the bytes on disk to match. */
    fun moveFile(id: String, folder: String): FileItem {
        val payload = JSONObject().put("folder", folder)
        val req = auth(Request.Builder().url("$BASE/v1/files/$id"))
            .patch(payload.toString().toRequestBody("application/json".toMediaType()))
            .build()
        client.newCall(req).execute().use { r ->
            val body = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw IOException("moveFile ${r.code}: ${body.take(160)}")
            return parseFile(JSONObject(body))
        }
    }

    /** Delete a folder and everything inside it (recursive on the server). */
    fun deleteFolder(folder: String) {
        val enc = java.net.URLEncoder.encode(folder, "UTF-8")
        val req = auth(Request.Builder().url("$BASE/v1/folders?folder=$enc")).delete().build()
        client.newCall(req).execute().use { r ->
            if (!r.isSuccessful) throw IOException("deleteFolder ${r.code}: ${r.body?.string().orEmpty().take(160)}")
        }
    }
}
