package com.rid.xorics

import okhttp3.Dns
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.InetAddress
import java.net.UnknownHostException
import java.util.concurrent.TimeUnit

/**
 * Thin client for the Xorics bridge. Same endpoints the web page and the Termux
 * probe use. All calls are blocking and must be invoked off the main thread.
 */
object Bridge {

    // Bridge exposed via `tailscale serve 8090`. Phone reaches it over the tailnet.
    const val HOST = "ridgames.tail893cf4.ts.net"
    const val BASE = "https://$HOST"

    // RIDGames's tailnet IP — stable for the node's life on the tailnet. Used ONLY as
    // a DNS fallback below, never in a URL: the URL must keep the hostname so TLS SNI
    // and certificate verification still see the .ts.net name.
    private val HOST_ADDR = byteArrayOf(100, 121, 204.toByte(), 85)

    /**
     * APP-B4: MagicDNS resolution dies on wake ("Unable to resolve host
     * ridgames.tail893cf4.ts.net", 2026-07-09 screenshots) independently of whether
     * the tunnel itself is up. System DNS stays authoritative; when it can't answer
     * for our host, pin the known tailnet IP. `getByAddress(hostname, addr)` keeps
     * the hostname attached to the address, so this bypasses DNS only — hostname
     * verification is untouched — and `tailscale serve` listens on the node's tailnet
     * IP:443, so the pinned route terminates at the same place. If the tunnel is
     * truly down, the pinned connect fails too and the caller's recovery path
     * (ChatActivity's reply watcher) takes it from there.
     */
    private object PinnedDns : Dns {
        override fun lookup(hostname: String): List<InetAddress> {
            try {
                val addrs = Dns.SYSTEM.lookup(hostname)
                if (addrs.isNotEmpty()) return addrs
            } catch (_: UnknownHostException) {
                // fall through to the pin
            }
            if (hostname.equals(HOST, ignoreCase = true)) {
                return listOf(InetAddress.getByAddress(hostname, HOST_ADDR))
            }
            throw UnknownHostException("no addresses for $hostname")
        }
    }

    // Must match XORICS_BRIDGE_TOKEN on the server if you set one. It is currently
    // unset, so any value is accepted.
    const val TOKEN = "xorics-app"

    private val client = OkHttpClient.Builder()
        .dns(PinnedDns)
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .build()

    // Promote re-verifies the whole suite in the sandbox server-side before it
    // touches the live tree — that alone can exceed the 120s default. Share the
    // pool, stretch only the read timeout.
    private val slowClient = client.newBuilder()
        .readTimeout(600, TimeUnit.SECONDS)
        .build()

    // APP-B4: sendMessage is the one non-idempotent call in the app. OkHttp's default
    // retryOnConnectionFailure can silently re-POST a request whose first attempt DID
    // reach the server (stale pooled connection; drop while reading the response) —
    // storing the user turn twice and burning a second model run. Turn it off here
    // only: the reply watcher in ChatActivity owns retry/verify semantics, and it
    // checks the db before ever letting a resend happen.
    private val sendClient = client.newBuilder()
        .retryOnConnectionFailure(false)
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

    /**
     * Send a user turn to a chat (server feeds prior turns back to the model); returns
     * the reply. APP-B4: callers must treat the return value as advisory — the reply's
     * delivery path of record is getMessages() (the db is truth). This call's job is to
     * CAUSE the turn; its response body is a courtesy that any tunnel drop can eat.
     */
    fun sendMessage(chatId: String, content: String): Msg {
        val payload = JSONObject().put("content", content)
        val req = auth(Request.Builder().url("$BASE/v1/chats/$chatId/messages"))
            .post(payload.toString().toRequestBody("application/json".toMediaType()))
            .build()
        sendClient.newCall(req).execute().use { r ->
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

    // ================= permissions API (operator tool grants) ================
    // Grants are PER-PROCESS and in-memory on the bridge: deny-all after every
    // bridge restart. Never cache — render from a fresh GET on open/resume, and
    // treat every POST response body as the new state (it is; no second GET).

    data class Perms(val privileged: List<String>, val granted: List<String>)

    private fun parsePerms(o: JSONObject): Perms {
        fun names(key: String): List<String> {
            val arr = o.getJSONArray(key)
            val out = ArrayList<String>(arr.length())
            for (i in 0 until arr.length()) out.add(arr.getString(i))
            return out
        }
        return Perms(names("privileged"), names("granted"))
    }

    /** Current grant state: which tools are privileged, which are granted right now. */
    fun getPermissions(): Perms {
        val req = auth(Request.Builder().url("$BASE/v1/permissions")).get().build()
        client.newCall(req).execute().use { r ->
            val body = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw IOException("getPermissions ${r.code}: ${body.take(160)}")
            return parsePerms(JSONObject(body))
        }
    }

    /** Grant a privileged tool; returns the new state. 400 if not a privileged tool. */
    fun grantTool(tool: String): Perms {
        val payload = JSONObject().put("tool", tool)
        val req = auth(Request.Builder().url("$BASE/v1/permissions/grant"))
            .post(payload.toString().toRequestBody("application/json".toMediaType()))
            .build()
        client.newCall(req).execute().use { r ->
            val body = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw IOException("grantTool ${r.code}: ${body.take(160)}")
            return parsePerms(JSONObject(body))
        }
    }

    /** Revoke a tool (idempotent on the server); returns the new state. 400 if not privileged. */
    fun revokeTool(tool: String): Perms {
        val payload = JSONObject().put("tool", tool)
        val req = auth(Request.Builder().url("$BASE/v1/permissions/revoke"))
            .post(payload.toString().toRequestBody("application/json".toMediaType()))
            .build()
        client.newCall(req).execute().use { r ->
            val body = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw IOException("revokeTool ${r.code}: ${body.take(160)}")
            return parsePerms(JSONObject(body))
        }
    }

    // ================= self-edit API (review / promote / discard) ============
    // APP-B3: the deliberate human gate on the front half of the self-improvement
    // loop. GET shows what a self-edit staged; NOTHING lands until the operator
    // explicitly promotes — and the server re-verifies in the sandbox before it
    // touches the live tree. Every POST response body IS the new state (it is;
    // no second GET), same contract as the permissions API above.

    data class SelfEdit(
        val files: List<String>,
        val diff: String,
        val task: String,
        val status: String?
    )

    private fun parseSelfEdit(o: JSONObject): SelfEdit {
        val arr = o.getJSONArray("files")
        val files = ArrayList<String>(arr.length())
        for (i in 0 until arr.length()) files.add(arr.getString(i))
        return SelfEdit(
            files,
            o.optString("diff"),
            o.optString("task"),
            if (o.has("status")) o.getString("status") else null
        )
    }

    /** What's pending for promotion right now: changed files, unified diff, task. */
    fun getSelfEdit(): SelfEdit {
        val req = auth(Request.Builder().url("$BASE/v1/selfedit")).get().build()
        client.newCall(req).execute().use { r ->
            val body = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw IOException("getSelfEdit ${r.code}: ${body.take(160)}")
            return parseSelfEdit(JSONObject(body))
        }
    }

    /**
     * Promote the pending self-edit: the server re-verifies it in the sandbox,
     * copies it into the live tree, commits — and pushes when `push` is true.
     * Slow by nature (the re-verify runs the whole suite), hence slowClient.
     */
    fun promoteSelfEdit(push: Boolean): SelfEdit {
        val payload = JSONObject().put("push", push)
        val req = auth(Request.Builder().url("$BASE/v1/selfedit/promote"))
            .post(payload.toString().toRequestBody("application/json".toMediaType()))
            .build()
        slowClient.newCall(req).execute().use { r ->
            val body = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw IOException("promoteSelfEdit ${r.code}: ${body.take(160)}")
            return parseSelfEdit(JSONObject(body))
        }
    }

    /** Throw the pending self-edit away; the live tree is untouched. */
    fun discardSelfEdit(): SelfEdit {
        val req = auth(Request.Builder().url("$BASE/v1/selfedit/discard"))
            .post("{}".toRequestBody("application/json".toMediaType()))
            .build()
        client.newCall(req).execute().use { r ->
            val body = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw IOException("discardSelfEdit ${r.code}: ${body.take(160)}")
            return parseSelfEdit(JSONObject(body))
        }
    }
}
