package com.rid.xorics

import android.content.Context
import android.net.Uri
import android.os.Bundle
import android.provider.OpenableColumns
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
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
 * File explorer over the /v1/files API: navigate folders, upload from the phone, move
 * files between folders, delete files and folders. Folders exist where files live (the
 * backend has no empty-folder concept), so "New folder" steps you into a new path that
 * becomes real once you upload there. Files are loose (no project) for now.
 */
class FilesActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                FilesScreen()
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FilesScreen() {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var folder by remember { mutableStateOf("/") }
    val files = remember { mutableStateListOf<Bridge.FileItem>() }
    val subfolders = remember { mutableStateListOf<String>() }
    val allFolders = remember { mutableStateListOf<String>() }
    var status by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var showNewFolder by remember { mutableStateOf(false) }
    var folderToDelete by remember { mutableStateOf<String?>(null) }
    var fileToMove by remember { mutableStateOf<Bridge.FileItem?>(null) }

    fun refresh() {
        scope.launch {
            busy = true
            status = ""
            try {
                val all = withContext(Dispatchers.IO) { Bridge.listFolders() }
                val here = withContext(Dispatchers.IO) { Bridge.listFiles(folder) }
                allFolders.clear(); allFolders.addAll(all)
                subfolders.clear(); subfolders.addAll(subfoldersOf(folder, all))
                files.clear(); files.addAll(here)
            } catch (e: Exception) {
                status = "error: ${e.message}"
            } finally {
                busy = false
            }
        }
    }

    LaunchedEffect(folder) { refresh() }

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) {
            scope.launch {
                busy = true
                status = "uploading…"
                try {
                    val name = withContext(Dispatchers.IO) { displayName(context, uri) }
                    val bytes = withContext(Dispatchers.IO) {
                        context.contentResolver.openInputStream(uri)?.use { it.readBytes() } ?: ByteArray(0)
                    }
                    withContext(Dispatchers.IO) { Bridge.uploadFile(name, bytes, folder) }
                    status = ""
                    refresh()
                } catch (e: Exception) {
                    status = "upload error: ${e.message}"
                } finally {
                    busy = false
                }
            }
        }
    }

    fun deleteFile(f: Bridge.FileItem) {
        scope.launch {
            try {
                withContext(Dispatchers.IO) { Bridge.deleteFile(f.id) }
                refresh()
            } catch (e: Exception) {
                status = "delete error: ${e.message}"
            }
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(folder, maxLines = 1) },
                navigationIcon = {
                    if (folder != "/") TextButton(onClick = { folder = parentOf(folder) }) { Text("Up") }
                },
                actions = {
                    TextButton(onClick = { showNewFolder = true }) { Text("New") }
                    TextButton(onClick = { picker.launch("*/*") }, enabled = !busy) { Text("Upload") }
                }
            )
        }
    ) { pad ->
        Column(Modifier.fillMaxSize().padding(pad)) {
            if (status.isNotEmpty()) {
                Text(
                    status,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 6.dp)
                )
            }
            if (busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            LazyColumn(Modifier.fillMaxSize()) {
                items(subfolders) { name ->
                    FolderRow(
                        name = name,
                        onOpen = { folder = joinPath(folder, name) },
                        onDelete = { folderToDelete = joinPath(folder, name) }
                    )
                }
                items(files) { f ->
                    FileRow(f, onMove = { fileToMove = f }, onDelete = { deleteFile(f) })
                }
                if (subfolders.isEmpty() && files.isEmpty() && !busy) {
                    item {
                        Text(
                            "Empty. Tap Upload to add a file here.",
                            modifier = Modifier.padding(24.dp),
                            style = MaterialTheme.typography.bodyMedium
                        )
                    }
                }
            }
        }
    }

    if (showNewFolder) {
        NewFolderDialog(
            onDismiss = { showNewFolder = false },
            onCreate = { name ->
                showNewFolder = false
                val clean = name.trim().trim('/')
                if (clean.isNotEmpty()) folder = joinPath(folder, clean)
            }
        )
    }

    folderToDelete?.let { fld ->
        DeleteFolderDialog(
            folder = fld,
            onDismiss = { folderToDelete = null },
            onConfirm = {
                folderToDelete = null
                scope.launch {
                    busy = true
                    status = "deleting…"
                    try {
                        withContext(Dispatchers.IO) { Bridge.deleteFolder(fld) }
                        status = ""
                        refresh()
                    } catch (e: Exception) {
                        status = "delete error: ${e.message}"
                    } finally {
                        busy = false
                    }
                }
            }
        )
    }

    fileToMove?.let { file ->
        MoveFileDialog(
            file = file,
            folders = allFolders.toList(),
            onDismiss = { fileToMove = null },
            onMove = { dest ->
                fileToMove = null
                scope.launch {
                    busy = true
                    status = "moving…"
                    try {
                        withContext(Dispatchers.IO) { Bridge.moveFile(file.id, dest) }
                        status = ""
                        refresh()
                    } catch (e: Exception) {
                        status = "move error: ${e.message}"
                    } finally {
                        busy = false
                    }
                }
            }
        )
    }
}

@Composable
private fun FolderRow(name: String, onOpen: () -> Unit, onDelete: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(
            "📁  $name",
            style = MaterialTheme.typography.bodyLarge,
            modifier = Modifier.weight(1f).clickable(onClick = onOpen).padding(vertical = 8.dp)
        )
        TextButton(onClick = onDelete) { Text("Delete") }
    }
}

@Composable
private fun FileRow(f: Bridge.FileItem, onMove: () -> Unit, onDelete: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column(Modifier.weight(1f)) {
            Text("📄  ${f.name}", style = MaterialTheme.typography.bodyLarge)
            Text(humanSize(f.size), style = MaterialTheme.typography.bodySmall)
        }
        TextButton(onClick = onMove) { Text("Move") }
        TextButton(onClick = onDelete) { Text("Delete") }
    }
}

@Composable
private fun NewFolderDialog(onDismiss: () -> Unit, onCreate: (String) -> Unit) {
    var name by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("New folder") },
        text = {
            OutlinedTextField(
                value = name,
                onValueChange = { name = it },
                singleLine = true,
                placeholder = { Text("folder name") }
            )
        },
        confirmButton = { TextButton(onClick = { onCreate(name) }) { Text("Create") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } }
    )
}

@Composable
private fun DeleteFolderDialog(folder: String, onDismiss: () -> Unit, onConfirm: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Delete folder?") },
        text = { Text("Delete \"$folder\" and everything inside it? This can't be undone.") },
        confirmButton = { TextButton(onClick = onConfirm) { Text("Delete") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } }
    )
}

@Composable
private fun MoveFileDialog(
    file: Bridge.FileItem,
    folders: List<String>,
    onDismiss: () -> Unit,
    onMove: (String) -> Unit
) {
    var dest by remember { mutableStateOf(file.folder) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Move ${file.name}") },
        text = {
            Column {
                OutlinedTextField(
                    value = dest,
                    onValueChange = { dest = it },
                    singleLine = true,
                    label = { Text("Destination folder") }
                )
                if (folders.isNotEmpty()) {
                    Spacer(Modifier.height(8.dp))
                    Text("Existing folders:", style = MaterialTheme.typography.bodySmall)
                    folders.forEach { fld ->
                        TextButton(onClick = { dest = fld }) { Text(fld) }
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = { onMove(dest) }) { Text("Move") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } }
    )
}

// ---- path helpers ----
private fun joinPath(current: String, name: String): String =
    if (current == "/") "/$name" else "$current/$name"

private fun parentOf(current: String): String {
    if (current == "/") return "/"
    val p = current.substringBeforeLast("/")
    return if (p.isEmpty()) "/" else p
}

/** Direct subfolders of [current], derived from the flat list of all folder paths. */
private fun subfoldersOf(current: String, all: List<String>): List<String> {
    val prefix = if (current == "/") "/" else "$current/"
    val names = sortedSetOf<String>()
    for (f in all) {
        if (f != current && f.startsWith(prefix)) {
            val rest = f.removePrefix(prefix)
            if (rest.isNotEmpty()) names.add(rest.substringBefore("/"))
        }
    }
    return names.toList()
}

private fun humanSize(bytes: Long): String = when {
    bytes >= 1_000_000 -> "%.1f MB".format(bytes / 1_000_000.0)
    bytes >= 1_000 -> "%.1f KB".format(bytes / 1_000.0)
    else -> "$bytes B"
}

private fun displayName(context: Context, uri: Uri): String {
    var name = "upload.bin"
    context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
        if (c.moveToFirst()) {
            val idx = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (idx >= 0) c.getString(idx)?.let { name = it }
        }
    }
    return name
}
