package com.rid.xorics

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/** Mode presets that prepend command prefixes before the message. */
enum class SendMode(val label: String, val prefix: String) {
    CHAT("Chat", "/chat "),
    POWER("Power", "/power "),
    CODE("Code", "/code "),
    PLAN("Plan", "/plan ");
}

/** A thin horizontal toolbar above the input bar. The mode dropdown lives on the left;
 * room for additional tool buttons to be added to the right. */
@Composable
fun ChatToolbar(
    currentMode: SendMode,
    onModeChange: (SendMode) -> Unit,
) {
    var menuOpen by remember { mutableStateOf(false) }

    Surface(tonalElevation = 1.dp) {
        Row(
            modifier = Modifier.fillMaxWidth().height(40.dp).padding(horizontal = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            TextButton(onClick = { menuOpen = true }) {
                Text(
                    text = currentMode.label,
                    style = MaterialTheme.typography.labelMedium,
                )
            }
            DropdownMenu(
                expanded = menuOpen,
                onDismissRequest = { menuOpen = false }
            ) {
                SendMode.entries.forEach { mode ->
                    DropdownMenuItem(
                        text = { Text(mode.label) },
                        onClick = {
                            onModeChange(mode)
                            menuOpen = false
                        }
                    )
                }
            }

            // Spacer — other toolbar items can go here
            Spacer(Modifier.weight(1f))
        }
    }
}

/** The message input row at the bottom of the chat screen. */
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
                maxLines = 5,
                enabled = enabled,
                shape = RoundedCornerShape(24.dp),
            )
            Spacer(Modifier.width(8.dp))
            Button(
                onClick = onSend,
                enabled = enabled && value.isNotBlank()
            ) {
                Text("Send")
            }
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
