package com.rid.xorics

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.SystemClock
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * The app's main screen: a memory-backed conversation with Xorics via the /v1/chats API.
 * One persistent chat for now (its id is remembered across launches); chat history,
 * projects, and the file explorer come in later increments. The "Voice" action opens the
 * existing control panel (MainActivity), which is untouched.
 */
class ChatActivity : ComponentActivity() {
    // Bumped on every onResume so the screen re-fetches grant state. Grants are
    // per-process on the bridge (deny-all after every restart) — never cache them
    // across a pause/resume.
    private val resumeTick = mutableIntStateOf(0)

    override fun onResume() {
        super.onResume()
        resumeTick.intValue++
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                ChatScreen(
                    onOpenVoice = { startActivity(Intent(this, MainActivity::class.java)) },
                    onOpenFiles = { startActivity(Intent(this, FilesActivity::class.java)) },
                    resumeTick = resumeTick.intValue
                )
            }
        }
    }
}

// --- APP-B2: permission-card trigger ------------------------------------------
// The gate's deny string (xorics.py _gate_privileged_call) is:
//   PERMISSION REQUIRED: tool '<name>' needs an operator grant — ask the operator
//   to run /grant <name>, then retry.
// The manager relays in its own words and can paraphrase the marker away, so this
// is best-effort by design: trigger on the marker or a "/grant <name>" mention,
// and cover misses with the manual Grants button in the top bar.
private val PERM_TOOL = Regex("tool '([^']+)'")
private val PERM_GRANT = Regex("/grant ([A-Za-z0-9_]+)")

/** Returns (this reply is asking for a grant, tool name if one could be extracted). */
private fun permissionAsk(reply: String): Pair<Boolean, String?> {
    val asked = reply.contains("PERMISSION REQUIRED", ignoreCase = true) ||
        PERM_GRANT.containsMatchIn(reply)
    if (!asked) return Pair(false, null)
    val tool = PERM_TOOL.find(reply)?.groupValues?.get(1)
        ?: PERM_GRANT.find(reply)?.groupValues?.get(1)
    return Pair(true, tool)
}

// --- APP-B4: delivery resilience -----------------------------------------------
// Until now the reply to a send was delivered exactly one way: as the body of that
// one blocking POST (Bridge.sendMessage). Server-side, api.py stores the user row
// BEFORE generation and the assistant row right after — both inside that same
// request — so any client-side drop (MagicDNS dead on wake, tunnel blip, 5G↔WiFi
// handover, the 120 s read timeout) lost only DELIVERY while the db quietly kept the
// truth. Proof: the 2026-07-09 screenshots — reply present after a manual reload,
// "Unable to resolve host" stuck in the banner. Nothing in the app re-asked for
// messages after creation (onResume refreshed grants only), so recovery was always
// manual.
//
// The fix inverts the contract: the db is the source of truth and getMessages() is
// the delivery path. The POST only CAUSES the turn; a watcher polls history until
// the reply is visible, survives any number of drops, verifies whether a failed
// POST actually landed before handing the text back for a resend (no silent
// double-turns), and the whole list re-syncs on every resume. The status banner
// clears on the first successful sync instead of sticking forever.
private const val POLL_MS = 2_500L            // watcher cadence while a reply is due
private const val POLL_SLOW_MS = 10_000L      // relaxed cadence for long tool turns
private const val POLL_SLOW_AFTER_MS = 60_000L
private const val WATCH_DEADLINE_MS = 15 * 60_000L
private const val ESCALATE_AFTER_MS = 8_000L  // drops that self-heal faster than this never reach the banner

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(onOpenVoice: () -> Unit, onOpenFiles: () -> Unit, resumeTick: Int) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val messages = remember { mutableStateListOf<Bridge.Msg>() }
    var input by remember { mutableStateOf("") }
    var sending by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("connecting…") }
    var chatId by remember { mutableStateOf<String?>(null) }
    var menuOpen by remember { mutableStateOf(false) }
    var chats by remember { mutableStateOf<List<Bridge.ChatMeta>>(emptyList()) }
    var watcherJob by remember { mutableStateOf<Job?>(null) }
    val listState = rememberLazyListState()
    var currentMode by remember { mutableStateOf(SendMode.CHAT) }

    // Permission card state (APP-B2). perms is only ever what the bridge last said.
    var perms by remember { mutableStateOf<Bridge.Perms?>(null) }
    var showPerms by remember { mutableStateOf(false) }
    var permHint by remember { mutableStateOf<String?>(null) }
    var permStatus by remember { mutableStateOf("") }

    fun refreshPerms() {
        scope.launch {
            try {
                perms = withContext(Dispatchers.IO) { Bridge.getPermissions() }
                permStatus = ""
            } catch (e: Exception) {
                permStatus = "error: ${e.message}"
            }
        }
    }

    /** Approve/Revoke. The POST response body IS the new state — no second GET. */
    fun setGrant(tool: String, grant: Boolean) {
        scope.launch {
            try {
                perms = withContext(Dispatchers.IO) {
                    if (grant) Bridge.grantTool(tool) else Bridge.revokeTool(tool)
                }
                permStatus = ""
            } catch (e: Exception) {
                permStatus = "error: ${e.message}"
            }
        }
    }

    // Self-edit review card state (APP-B3). selfEdit is only ever what the bridge
    // last said. The card is the human gate: the diff gets READ here, and promote
    // or discard only fire on an explicit (armed) operator tap — never automatically.
    var selfEdit by remember { mutableStateOf<Bridge.SelfEdit?>(null) }
    var showEdits by remember { mutableStateOf(false) }
    var editsBusy by remember { mutableStateOf(false) }
    var editsStatus by remember { mutableStateOf("") }

    fun refreshEdits() {
        scope.launch {
            try {
                selfEdit = withContext(Dispatchers.IO) { Bridge.getSelfEdit() }
                editsStatus = ""
            } catch (e: Exception) {
                editsStatus = "error: ${e.message}"
            }
        }
    }

    /** Promote or discard. The POST response body IS the new state — no second GET. */
    fun resolveEdits(promote: Boolean) {
        if (editsBusy) return
        editsBusy = true
        editsStatus = if (promote) "promoting — sandbox re-verify runs first, this takes a while…"
                      else "discarding…"
        scope.launch {
            try {
                selfEdit = withContext(Dispatchers.IO) {
                    if (promote) Bridge.promoteSelfEdit(push = true) else Bridge.discardSelfEdit()
                }
                editsStatus = selfEdit?.status ?: ""
            } catch (e: Exception) {
                editsStatus = "error: ${e.message}"
            } finally {
                editsBusy = false
            }
        }
    }

    /** One history fetch; null when the network says no (callers just try again). */
    suspend fun syncOnce(id: String): List<Bridge.Msg>? = withContext(Dispatchers.IO) {
        try {
            Bridge.getMessages(id)
        } catch (_: Exception) {
            null
        }
    }

    /**
     * Server truth → screen; returns whether anything changed. History is append-only
     * and this screen is its only writer, so equal length ⇒ equal content; on any
     * length difference a full replace keeps the reasoning unarguable.
     */
    fun reconcile(server: List<Bridge.Msg>): Boolean {
        if (server.size == messages.size) return false
        messages.clear()
        messages.addAll(server)
        return true
    }

    // Grants are per-process on the bridge, so re-fetch on open and on every resume
    // rather than trusting anything cached. Chat truth lives in the db and can move
    // while we're backgrounded (a reply that finished after the screen slept) — re-pull
    // it too. Skip the chat sync while a send's watcher owns the list; it's already
    // polling.
    LaunchedEffect(resumeTick) {
        refreshPerms()
        val id = chatId ?: return@LaunchedEffect
        if (sending) return@LaunchedEffect
        val server = syncOnce(id) ?: return@LaunchedEffect
        if (reconcile(server) && messages.isNotEmpty()) listState.scrollToItem(messages.size - 1)
        status = ""  // reachable again — retire any stale error banner
    }

    // Load (or create) the persistent chat, then fetch its history.
    LaunchedEffect(Unit) {
        try {
            val prefs = context.getSharedPreferences("xorics", Context.MODE_PRIVATE)
            val existing = prefs.getString("chatId", null)
            val id: String = existing
                ?: withContext(Dispatchers.IO) { Bridge.createChat() }.also { newId ->
                    prefs.edit().putString("chatId", newId).apply()
                }
            chatId = id
            val hist = withContext(Dispatchers.IO) { Bridge.getMessages(id) }
            messages.clear()
            messages.addAll(hist)
            status = ""
            if (messages.isNotEmpty()) listState.scrollToItem(messages.size - 1)
        } catch (e: Exception) {
            status = "connect error: ${e.message}"
        }
    }

    fun send() {
        val text = (currentMode.prefix + input.trim()).trim()
        val id = chatId
        if (id == null || sending) return
        if (text.isEmpty() || text == currentMode.prefix) return
        input = ""
        val baseline = messages.size  // index the user row will occupy in server truth
        messages.add(Bridge.Msg("user", text))
        sending = true
        status = "thinking…"

        // The POST causes the turn; it is no longer the delivery path. Its failure only
        // tells the watcher to start checking whether the send landed at all. Both
        // coroutines run on Main, so the flags need no synchronization. NOTE: a failure
        // here is NOT shown yet — most drops (a WiFi↔5G handover killing the socket
        // mid-read) self-heal within one watcher tick, and flashing "connection dropped"
        // for those just trains the operator to ignore the banner. The watcher escalates
        // only if the drop outlives ESCALATE_AFTER_MS without the row being confirmed.
        var postFailed = false
        var postErr: String? = null
        scope.launch {
            try {
                withContext(Dispatchers.IO) { Bridge.sendMessage(id, text) }
            } catch (e: Exception) {
                postFailed = true
                postErr = e.message?.take(80)
            }
        }

        // The reply watcher: sole renderer of the reply, immune to the POST's fate.
        // Any number of drops between here and the reply just cost a poll tick each.
        watcherJob = scope.launch {
            listState.animateScrollToItem(messages.size - 1)
            val start = SystemClock.elapsedRealtime()
            var absentConfirms = 0
            var confirmedLanded = false
            try {
                while (true) {
                    if (chatId != id) return@launch
                    val elapsed = SystemClock.elapsedRealtime() - start
                    if (elapsed > WATCH_DEADLINE_MS) {
                        syncOnce(id)?.let { reconcile(it) }
                        status = "no reply after 15 min — check the bridge (journalctl); will re-sync on resume"
                        return@launch
                    }
                    delay(if (elapsed > POLL_SLOW_AFTER_MS) POLL_SLOW_MS else POLL_MS)
                    // Escalate only when a drop has outlived the quiet window without the
                    // row being confirmed server-side — transient socket kills self-heal
                    // below and never surface.
                    if (postFailed && !confirmedLanded &&
                        SystemClock.elapsedRealtime() - start > ESCALATE_AFTER_MS) {
                        status = "reconnecting (${postErr ?: "connection dropped"}) — will fetch the reply when the tunnel returns"
                    }
                    val server = syncOnce(id) ?: continue  // tunnel down — just try again
                    val landed = server.size > baseline &&
                        server[baseline].role == "user" && server[baseline].content == text
                    when {
                        landed && server.size >= baseline + 2 -> {
                            // The reply is in the db — render from truth and finish.
                            reconcile(server)
                            status = ""
                            val (asked, tool) = permissionAsk(server[baseline + 1].content)
                            if (asked) {
                                permHint = tool
                                showPerms = true
                                refreshPerms()
                            }
                            return@launch
                        }
                        landed -> {
                            // Stored server-side, still generating — keep waiting. Also
                            // retires a "reconnecting" escalation once the row is confirmed.
                            absentConfirms = 0
                            confirmedLanded = true
                            status = "thinking…"
                        }
                        // landed but no assistant reply yet — reconcile so the empty response
                        // is shown and the thinking spinner clears instead of hanging forever.
                        server.size == baseline + 1 -> {
                            reconcile(server)
                            status = ""
                            return@launch
                        }
                        postFailed || server.size != baseline -> {
                            // Our row is missing after the POST gave up, or history diverged
                            // (something else sits where our turn should be). Two consecutive
                            // confirmations rule out racing a row insert that's mid-flight;
                            // then hand the text back for an explicit human retry — never a
                            // silent one (Bridge.sendClient disables OkHttp's, for the same
                            // reason: a hidden re-POST can store the turn twice).
                            absentConfirms++
                            if (absentConfirms >= 2) {
                                reconcile(server)  // drops the optimistic bubble; server is truth
                                input = text
                                status = "send didn't reach the bridge${postErr?.let { " ($it)" } ?: ""} — tap Send to retry"
                                return@launch
                            }
                        }
                        else -> Unit  // POST still in flight, row not stored yet — normal early ticks
                    }
                }
            } finally {
                sending = false
                if (messages.isNotEmpty()) listState.animateScrollToItem(messages.size - 1)
            }
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Xorics") },
                actions = {
                    IconButton(onClick = { menuOpen = true; scope.launch { chats = withContext(Dispatchers.IO) { try { Bridge.listChats() } catch (_: Exception) { emptyList() } } } }) {
                        Icon(Icons.Default.Menu, contentDescription = "Menu")
                    }
                    DropdownMenu(
                        expanded = menuOpen,
                        onDismissRequest = { menuOpen = false }
                    ) {
                        DropdownMenuItem(
                            text = { Text("New chat") },
                            onClick = {
                                menuOpen = false
                                watcherJob?.cancel()
                                scope.launch {
                                    val newId = withContext(Dispatchers.IO) { Bridge.createChat() }
                                    context.getSharedPreferences("xorics", Context.MODE_PRIVATE).edit().putString("chatId", newId).apply()
                                    chatId = newId
                                    messages.clear()
                                    status = ""
                                }
                            }
                        )
                        HorizontalDivider()
                        chats.forEach { c ->
                            DropdownMenuItem(
                                text = { Text(c.title.ifBlank { "Untitled chat" }) },
                                onClick = {
                                    menuOpen = false
                                    watcherJob?.cancel()
                                    context.getSharedPreferences("xorics", Context.MODE_PRIVATE)
                                        .edit().putString("chatId", c.id).apply()
                                    chatId = c.id
                                    scope.launch {
                                        val hist = withContext(Dispatchers.IO) {
                                            try { Bridge.getMessages(c.id) } catch (_: Exception) { null }
                                        }
                                        if (chatId == c.id) {
                                            if (hist != null) {
                                                messages.clear()
                                                messages.addAll(hist)
                                                status = ""
                                                if (messages.isNotEmpty()) listState.scrollToItem(messages.size - 1)
                                            } else {
                                                status = "couldn't load history"
                                            }
                                        }
                                    }
                                },
                                trailingIcon = {
                                    IconButton(
                                        onClick = {
                                            scope.launch {
                                                withContext(Dispatchers.IO) { Bridge.deleteChat(c.id) }
                                                chats = withContext(Dispatchers.IO) {
                                                    try { Bridge.listChats() } catch (_: Exception) { emptyList() }
                                                }
                                                // If we deleted the current chat, switch to the first available one
                                                if (chatId == c.id) {
                                                    val first = chats.firstOrNull()
                                                    if (first != null) {
                                                        context.getSharedPreferences("xorics", Context.MODE_PRIVATE)
                                                            .edit().putString("chatId", first.id).apply()
                                                        chatId = first.id
                                                        messages.clear()
                                                        status = ""
                                                    } else {
                                                        // No chats left — create a new one
                                                        val newId = withContext(Dispatchers.IO) { Bridge.createChat() }
                                                        context.getSharedPreferences("xorics", Context.MODE_PRIVATE)
                                                            .edit().putString("chatId", newId).apply()
                                                        chatId = newId
                                                        messages.clear()
                                                        status = ""
                                                    }
                                                }
                                            }
                                        }
                                    ) {
                                        Icon(Icons.Default.Delete, contentDescription = "Delete chat", tint = MaterialTheme.colorScheme.error)
                                    }
                                }
                            )
                        }
                        if (chats.isNotEmpty()) HorizontalDivider()
                        DropdownMenuItem(
                            text = { Text("Edits") },
                            onClick = { menuOpen = false; showEdits = true; refreshEdits() }
                        )
                        DropdownMenuItem(
                            text = { Text("Grants") },
                            onClick = { menuOpen = false; permHint = null; showPerms = true; refreshPerms() }
                        )
                        DropdownMenuItem(
                            text = { Text("Files") },
                            onClick = { menuOpen = false; onOpenFiles() }
                        )
                        DropdownMenuItem(
                            text = { Text("Voice") },
                            onClick = { menuOpen = false; onOpenVoice() }
                        )
                    }
                }
            )
        }
    ) { pad ->
        // Keyboard rider (2026-07-09): pairs with android:windowSoftInputMode="adjustResize"
        // on this activity. Resize stops the system panning the whole window (which shoved
        // the TopAppBar off-screen); imePadding() consumes the IME inset so the input bar
        // rides above the keyboard instead of being covered by it.
        Column(Modifier.fillMaxSize().padding(pad).imePadding()) {
            LazyColumn(
                state = listState,
                modifier = Modifier.weight(1f).fillMaxWidth(),
                contentPadding = PaddingValues(12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                items(messages) { m -> MessageBubble(m) }
            }
            if (status.isNotEmpty()) {
                Text(
                    status,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp)
                )
            }
            ChatToolbar(
                currentMode = currentMode,
                onModeChange = { currentMode = it },
            )
            InputBar(
                value = input,
                onValue = { input = it },
                onSend = { send() },
                enabled = !sending && chatId != null,
            )
        }
    }

    if (showPerms) {
        PermissionsCard(
            perms = perms,
            hint = permHint,
            status = permStatus,
            onGrant = { setGrant(it, true) },
            onRevoke = { setGrant(it, false) },
            onRefresh = { refreshPerms() },
            onClose = { showPerms = false; permHint = null }
        )
    }

    if (showEdits) {
        SelfEditCard(
            edit = selfEdit,
            busy = editsBusy,
            status = editsStatus,
            onPromote = { resolveEdits(promote = true) },
            onDiscard = { resolveEdits(promote = false) },
            onRefresh = { refreshEdits() },
            onClose = { showEdits = false }
        )
    }
}


