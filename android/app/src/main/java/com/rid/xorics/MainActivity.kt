package com.rid.xorics

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.widget.Button
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

class MainActivity : AppCompatActivity() {

    private lateinit var status: TextView
    private lateinit var transcript: TextView
    private lateinit var reply: TextView
    private lateinit var talk: Button
    private lateinit var listen: Button
    private lateinit var wake: Button
    private lateinit var forceStop: Button
    // private lateinit var diagView: TextView   // DEBUG: on-screen audio diagnostic

    private val recordMs = 6000L
    private var batteryPrompted = false

    // --- one-shot (Talk button) mic permission ---
    private val micPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) runRoundtrip()
            else setStatus("microphone permission denied")
        }

    // --- background-listening permissions ---
    private val micForService =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) ensureAndStartService()
            else setStatus("microphone permission needed to listen")
        }
    private val notifPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) {
            // notification is optional; start regardless of the result
            startListeningService()
        }
    private val notifForWake =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) {
            startWakeService()
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        status = findViewById(R.id.status)
        transcript = findViewById(R.id.transcript)
        reply = findViewById(R.id.reply)
        talk = findViewById(R.id.talk)
        listen = findViewById(R.id.listen)
        wake = findViewById(R.id.wake)
        forceStop = findViewById(R.id.forceStop)
        // diagView = findViewById(R.id.diag)   // DEBUG

        talk.setOnClickListener {
            if (hasMic()) runRoundtrip() else micPermission.launch(Manifest.permission.RECORD_AUDIO)
        }
        listen.setOnClickListener { toggleListening() }
        wake.setOnClickListener { toggleWake() }
        forceStop.setOnClickListener { forceStopAndExit() }
    }

    override fun onResume() {
        super.onResume()
        updateListenButton()
        updateWakeButton()
    }

    private fun hasMic() =
        checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    private fun setStatus(s: String) = runOnUiThread { status.text = s }
    // private fun diag(s: String) = runOnUiThread { diagView.append(s + "\n") }   // DEBUG

    private fun updateListenButton() {
        listen.text = if (VoiceService.running) "Stop Listening" else "Start Listening"
    }

    private fun updateWakeButton() {
        wake.text = if (WakeService.running) "Stop Wake" else "Wake Word Test"
    }

    // ---- wake word (Vosk, on-device) ----
    private fun toggleWake() {
        if (WakeService.running) {
            startService(Intent(this, WakeService::class.java).setAction(WakeService.ACTION_STOP))
            wake.text = "Wake Word Test"
        } else if (!hasMic()) {
            setStatus("grant mic first — tap Talk once")
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            notifForWake.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            startWakeService()
        }
    }

    private fun startWakeService() {
        requestBatteryExemptionIfNeeded()
        ContextCompat.startForegroundService(this, Intent(this, WakeService::class.java))
        wake.text = "Stop Wake"
    }

    // ---- background listening ----
    private fun toggleListening() {
        if (VoiceService.running) {
            startService(Intent(this, VoiceService::class.java).setAction(VoiceService.ACTION_STOP))
            listen.text = "Start Listening"
        } else {
            ensureAndStartService()
        }
    }

    private fun ensureAndStartService() {
        if (!hasMic()) {
            micForService.launch(Manifest.permission.RECORD_AUDIO)
            return
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
            return
        }
        startListeningService()
    }

    private fun startListeningService() {
        // Without this, Doze defers the bridge network calls while the screen is off,
        // so replies don't arrive until you unlock. Whitelisting exempts the app.
        requestBatteryExemptionIfNeeded()
        ContextCompat.startForegroundService(this, Intent(this, VoiceService::class.java))
        listen.text = "Stop Listening"
    }

    private fun requestBatteryExemptionIfNeeded() {
        if (batteryPrompted) return
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        if (!pm.isIgnoringBatteryOptimizations(packageName)) {
            batteryPrompted = true
            try {
                startActivity(
                    Intent(
                        Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                        Uri.parse("package:$packageName")
                    )
                )
            } catch (_: Exception) {
            }
        }
    }

    /**
     * Hard kill switch for testing: tear down the service and kill the whole process so the
     * OS immediately reclaims the mic, audio output, and wake lock. With START_NOT_STICKY the
     * service won't be auto-recreated. If the app is too wedged to tap this, use
     * Settings > Apps > Xorics > Force stop, which does the same thing.
     */
    private fun forceStopAndExit() {
        try {
            startService(Intent(this, VoiceService::class.java).setAction(VoiceService.ACTION_STOP))
        } catch (_: Exception) {
        }
        finishAndRemoveTask()
        android.os.Process.killProcess(android.os.Process.myPid())
    }

    // ---- one-shot foreground roundtrip ----
    private fun runRoundtrip() {
        lifecycleScope.launch {
            talk.isEnabled = false
            // diagView.text = ""   // DEBUG
            try {
                val clip = "${cacheDir.absolutePath}/clip.m4a"

                setStatus("recording ${recordMs / 1000}s…")
                val rec = withContext(Dispatchers.IO) { Audio.startRecording(clip) }
                delay(recordMs)
                withContext(Dispatchers.IO) { Audio.stopRecording(rec) }

                setStatus("transcribing…")
                val bytes = withContext(Dispatchers.IO) { File(clip).readBytes() }
                val you = withContext(Dispatchers.IO) { Bridge.stt(bytes) }
                if (you.isBlank()) {
                    setStatus("(didn't catch that)")
                    return@launch
                }
                transcript.text = "you: $you"

                setStatus("thinking…")
                val answer = withContext(Dispatchers.IO) { Bridge.chat(you) }
                reply.text = "xorics: $answer"

                setStatus("speaking…")
                val audio = withContext(Dispatchers.IO) { Bridge.tts(answer) }
                val replyPath = "${cacheDir.absolutePath}/reply.wav"
                withContext(Dispatchers.IO) { File(replyPath).writeBytes(audio) }
                Audio.play(replyPath)
                // Audio.play(replyPath) { line -> diag(line) }   // DEBUG: show audio diagnostics
                setStatus("done — tap to talk")
            } catch (e: Exception) {
                setStatus("error: ${e.message}")
            } finally {
                talk.isEnabled = true
            }
        }
    }
}
