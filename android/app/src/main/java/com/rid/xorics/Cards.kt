package com.rid.xorics

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp

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

/**
 * Self-edit review card (APP-B3): the deliberate human gate on the front half of
 * the self-improvement loop. The operator READS the diff here, then explicitly
 * promotes (server re-verifies in the sandbox, commits, pushes) or discards.
 * Both destructive actions are armed behind a second tap; nothing auto-promotes.
 */
@Composable
fun SelfEditCard(
    edit: Bridge.SelfEdit?,
    busy: Boolean,
    status: String,
    onPromote: () -> Unit,
    onDiscard: () -> Unit,
    onRefresh: () -> Unit,
    onClose: () -> Unit
) {
    // "promote" | "discard" | null — the first tap arms, the second fires.
    var arm by remember { mutableStateOf<String?>(null) }
    AlertDialog(
        onDismissRequest = onClose,
        title = { Text("Pending self-edit") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                when {
                    edit == null -> Text("loading…")
                    edit.files.isEmpty() ->
                        Text("nothing pending — the workspace matches the live tree")
                    else -> {
                        if (edit.task.isNotEmpty()) {
                            Text("Task: ${edit.task}", style = MaterialTheme.typography.bodySmall)
                        }
                        Text(
                            edit.files.joinToString("\n"),
                            color = MaterialTheme.colorScheme.primary
                        )
                        Text(
                            edit.diff,
                            fontFamily = FontFamily.Monospace,
                            style = MaterialTheme.typography.bodySmall,
                            modifier = Modifier
                                .heightIn(max = 260.dp)
                                .verticalScroll(rememberScrollState())
                                .horizontalScroll(rememberScrollState())
                        )
                        Row(
                            Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            if (arm == "discard") {
                                Button(onClick = { arm = null; onDiscard() }, enabled = !busy) {
                                    Text("Confirm discard")
                                }
                            } else {
                                TextButton(onClick = { arm = "discard" }, enabled = !busy) {
                                    Text("Discard")
                                }
                            }
                            if (arm == "promote") {
                                Button(onClick = { arm = null; onPromote() }, enabled = !busy) {
                                    Text("Confirm promote")
                                }
                            } else {
                                TextButton(onClick = { arm = "promote" }, enabled = !busy) {
                                    Text("Promote + push")
                                }
                            }
                        }
                    }
                }
                if (status.isNotEmpty()) {
                    Text(status, style = MaterialTheme.typography.bodySmall)
                }
            }
        },
        confirmButton = { TextButton(onClick = onClose) { Text("Close") } },
        dismissButton = {
            TextButton(onClick = { arm = null; onRefresh() }, enabled = !busy) { Text("Refresh") }
        }
    )
}
