package com.rid.xorics

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
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
    val listState = rememberLazyListState()

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

    // Grants are per-process on the bridge, so re-fetch on open and on every
    // resume rather than trusting anything cached.
    LaunchedEffect(resumeTick) { refreshPerms() }

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
        val text = input.trim()
        val id = chatId
        if (text.isEmpty() || id == null || sending) return
        input = ""
        messages.add(Bridge.Msg("user", text))
        sending = true
        status = "thinking…"
        scope.launch {
            listState.animateScrollToItem(messages.size - 1)
            try {
                val reply = withContext(Dispatchers.IO) { Bridge.sendMessage(id, text) }
                messages.add(reply)
                status = ""
                val (asked, tool) = permissionAsk(reply.content)
                if (asked) {
                    permHint = tool
                    showPerms = true
                    refreshPerms()
                }
            } catch (e: Exception) {
                status = "error: ${e.message}"
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
                    TextButton(onClick = { permHint = null; showPerms = true; refreshPerms() }) { Text("Grants") }
                    TextButton(onClick = onOpenFiles) { Text("Files") }
                    TextButton(onClick = onOpenVoice) { Text("Voice") }
                }
            )
        }
    ) { pad ->
        Column(Modifier.fillMaxSize().padding(pad)) {
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
            InputBar(
                value = input,
                onValue = { input = it },
                onSend = { send() },
                enabled = !sending && chatId != null
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
}

@Composable
fun MessageBubble(m: Bridge.Msg) {
    val isUser = m.role == "user"
    val bubbleColor =
        if (isUser) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant
    val textColor =
        if (isUser) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurfaceVariant
    Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start
    ) {
        Surface(
            color = bubbleColor,
            shape = RoundedCornerShape(16.dp),
            modifier = Modifier.widthIn(max = 300.dp)
        ) {
            Text(
                m.content,
                color = textColor,
                modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp)
            )
        }
    }
}

@Composable
fun InputBar(value: String, onValue: (String) -> Unit, onSend: () -> Unit, enabled: Boolean) {
    Surface(tonalElevation = 2.dp) {
        Row(
            Modifier.fillMaxWidth().padding(8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            OutlinedTextField(
                value = value,
                onValueChange = onValue,
                modifier = Modifier.weight(1f),
                placeholder = { Text("Message Xorics") },
                maxLines = 5
            )
            Spacer(Modifier.width(8.dp))
            Button(onClick = onSend, enabled = enabled && value.isNotBlank()) {
                Text("Send")
            }
        }
    }
}

/**
 * Operator grant card (APP-B2). Renders only what the bridge reports: privileged
 * tools with their current grant state, Approve to grant, Revoke to drop. Every
 * state shown is the bridge's latest response body, never a cache.
 */
@Composable
fun PermissionsCard(
    perms: Bridge.Perms?,
    hint: String?,
    status: String,
    onGrant: (String) -> Unit,
    onRevoke: (String) -> Unit,
    onRefresh: () -> Unit,
    onClose: () -> Unit
) {
    AlertDialog(
        onDismissRequest = onClose,
        title = { Text("Tool permissions") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (hint != null) {
                    Text(
                        "Xorics is asking for '$hint'.",
                        color = MaterialTheme.colorScheme.primary
                    )
                }
                when {
                    perms == null -> Text("loading…")
                    perms.privileged.isEmpty() -> Text("no privileged tools")
                    else -> perms.privileged.forEach { tool ->
                        val granted = tool in perms.granted
                        Row(
                            Modifier.fillMaxWidth(),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Column(Modifier.weight(1f)) {
                                Text(tool)
                                Text(
                                    if (granted) "granted" else "denied",
                                    style = MaterialTheme.typography.bodySmall
                                )
                            }
                            if (granted) {
                                TextButton(onClick = { onRevoke(tool) }) { Text("Revoke") }
                            } else {
                                Button(onClick = { onGrant(tool) }) { Text("Approve") }
                            }
                        }
                    }
                }
                Text(
                    "Per bridge process — deny-all after every bridge restart.",
                    style = MaterialTheme.typography.bodySmall
                )
                if (status.isNotEmpty()) {
                    Text(
                        status,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error
                    )
                }
            }
        },
        confirmButton = { TextButton(onClick = onClose) { Text("Close") } },
        dismissButton = { TextButton(onClick = onRefresh) { Text("Refresh") } }
    )
}
