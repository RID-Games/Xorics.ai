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
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                ChatScreen(
                    onOpenVoice = { startActivity(Intent(this, MainActivity::class.java)) },
                    onOpenFiles = { startActivity(Intent(this, FilesActivity::class.java)) }
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(onOpenVoice: () -> Unit, onOpenFiles: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val messages = remember { mutableStateListOf<Bridge.Msg>() }
    var input by remember { mutableStateOf("") }
    var sending by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("connecting…") }
    var chatId by remember { mutableStateOf<String?>(null) }
    val listState = rememberLazyListState()

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
