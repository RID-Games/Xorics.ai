package com.rid.xorics

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.os.VibrationEffect
import android.os.Vibrator
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import org.json.JSONObject
import org.vosk.LibVosk
import org.vosk.LogLevel
import org.vosk.Model
import org.vosk.Recognizer
import org.vosk.android.RecognitionListener
import org.vosk.android.SpeechService
import java.io.File

/**
 * Rung 3a: proves on-device wake-word detection with Vosk.
 *
 * Runs entirely locally — Vosk's SpeechService holds one mic stream and transcribes
 * continuously. It does NOT call the bridge. On each (partial) result it:
 *   - shows the live transcript in the notification (so we can SEE what "Xorics"
 *     actually transcribes as, and tune WAKE_VARIANTS from real output), and
 *   - if the text matches a wake variant, vibrates + flashes the notification.
 *
 * The model is loaded from the app's external files dir (push it with adb), not from
 * assets — that avoids the AAPT-compression and StorageService.unpack pitfalls.
 *
 * Next rung wires a detected wake word into VoiceService's capture->reply path.
 */
class WakeService : Service(), RecognitionListener {

    companion object {
        const val CHANNEL_ID = "xorics_wake"
        const val NOTIF_ID = 2
        const val ACTION_STOP = "com.rid.xorics.WAKE_STOP"

        @Volatile
        var running = false
            private set

        // Starter set — both ZOR-ics and SOAR-ics, plus likely Vosk approximations.
        // Tune this once we can see what the small model emits for the spoken word.
        val WAKE_VARIANTS = listOf(
            "xorics", "zorics", "sorics", "zoric", "soric", "zorix", "sorix", "sorx", "zorx",
            "zorro ic", "zor ic", "sore ic", "soar ic", "sore ix", "zore ix", "sore x", "zor x"
        )
    }

    private var model: Model? = null
    private var speechService: SpeechService? = null
    private var wakeLock: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopEverything()
            return START_NOT_STICKY
        }
        if (running) return START_NOT_STICKY

        createChannel()
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else 0
        ServiceCompat.startForeground(this, NOTIF_ID, buildNotification("Loading model…"), type)

        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "xorics:wake").apply {
            setReferenceCounted(false)
            acquire(60 * 60 * 1000L)
        }

        running = true
        // Model load + recognizer init is slow IO; do it off the main thread.
        Thread { initAndStart() }.start()
        return START_NOT_STICKY
    }

    private fun initAndStart() {
        try {
            LibVosk.setLogLevel(LogLevel.INFO)
            val dir = File(getExternalFilesDir(null), "model")
            if (!File(dir, "am").exists()) {
                notify("Model missing — adb push it to .../files/model")
                selfStop()
                return
            }
            model = Model(dir.absolutePath)
            val rec = Recognizer(model, 16000.0f)
            val svc = SpeechService(rec, 16000.0f)
            speechService = svc
            svc.startListening(this)
            notify("Listening for wake word…")
        } catch (e: Exception) {
            notify("init err: ${e.message?.take(60)}")
            selfStop()
        }
    }

    // --- RecognitionListener (callbacks arrive on the main thread) ---

    override fun onPartialResult(hypothesis: String?) {
        val t = textOf(hypothesis, "partial")
        if (t.isNotBlank()) notify("… $t")
        if (matches(t)) onWake(t)
    }

    override fun onResult(hypothesis: String?) {
        val t = textOf(hypothesis, "text")
        if (t.isNotBlank()) notify("heard: $t")
        if (matches(t)) onWake(t)
    }

    override fun onFinalResult(hypothesis: String?) {
        val t = textOf(hypothesis, "text")
        if (matches(t)) onWake(t)
    }

    override fun onError(e: Exception?) {
        notify("err: ${e?.message?.take(60)}")
    }

    override fun onTimeout() {}

    private fun textOf(hypothesis: String?, key: String): String =
        try {
            JSONObject(hypothesis ?: "{}").optString(key, "").trim()
        } catch (_: Exception) {
            ""
        }

    private fun matches(text: String): Boolean {
        val s = text.lowercase()
        return s.isNotBlank() && WAKE_VARIANTS.any { s.contains(it) }
    }

    private fun onWake(text: String) {
        vibrate()
        notify("✓ WAKE: $text")
    }

    private fun vibrate() {
        try {
            @Suppress("DEPRECATION")
            val vib = getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
            vib.vibrate(VibrationEffect.createOneShot(250, VibrationEffect.DEFAULT_AMPLITUDE))
        } catch (_: Exception) {
        }
    }

    private fun stopEverything() {
        running = false
        try {
            speechService?.stop()
            speechService?.shutdown()
        } catch (_: Exception) {
        }
        speechService = null
        model = null
        releaseWakeLock()
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun selfStop() = stopEverything()

    private fun releaseWakeLock() {
        try {
            wakeLock?.let { if (it.isHeld) it.release() }
        } catch (_: Exception) {
        }
        wakeLock = null
    }

    override fun onDestroy() {
        running = false
        try {
            speechService?.stop()
            speechService?.shutdown()
        } catch (_: Exception) {
        }
        releaseWakeLock()
        super.onDestroy()
    }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val ch = NotificationChannel(
                CHANNEL_ID, "Xorics wake word", NotificationManager.IMPORTANCE_LOW
            ).apply { description = "On-device wake-word detection" }
            (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
                .createNotificationChannel(ch)
        }
    }

    private fun buildNotification(text: String): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val stop = PendingIntent.getService(
            this, 2, Intent(this, WakeService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Xorics wake word")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(open)
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, "Stop", stop)
            .build()
    }

    private fun notify(text: String) {
        if (!running) return
        (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
            .notify(NOTIF_ID, buildNotification(text))
    }
}
