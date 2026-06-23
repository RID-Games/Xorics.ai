package com.rid.xorics

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.MediaRecorder
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import java.io.File

/**
 * Rung 2: runs the voice loop in the background as a microphone foreground service,
 * so Xorics answers with the app closed / screen locked.
 *
 * IMPORTANT: this service keeps running after the app is swiped away (that is what
 * makes it work while locked). It is stopped via the notification's Stop action or
 * the in-app toggle — NOT by closing the app.
 *
 * The loop is a fixed-window listener: record ~6s, transcribe, and if something
 * intelligible was said, reply and speak it, then listen again. It is deliberately
 * crude and always-listening, which holds the mic continuously and disrupts other
 * audio (Bluetooth, media). A wake word + VAD (next rungs) make it efficient. To
 * limit damage, the service auto-stops after a stretch of silence.
 */
class VoiceService : Service() {

    companion object {
        const val CHANNEL_ID = "xorics_voice"
        const val NOTIF_ID = 1
        const val ACTION_STOP = "com.rid.xorics.STOP"

        @Volatile
        var running = false
            private set
    }

    private var loopThread: Thread? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private val recordMs = 6000L
    private val maxIdleWindows = 24 // ~3 min of silence -> auto-stop
    private var idleWindows = 0

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
        ServiceCompat.startForeground(this, NOTIF_ID, buildNotification("Starting…"), type)

        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "xorics:voice").apply {
            setReferenceCounted(false)
            acquire(60 * 60 * 1000L) // 1h safety cap
        }

        running = true
        idleWindows = 0
        loopThread = Thread { loop() }.also { it.start() }
        return START_NOT_STICKY
    }

    private fun loop() {
        val clip = "${cacheDir.absolutePath}/svc_clip.m4a"
        val replyPath = "${cacheDir.absolutePath}/svc_reply.wav"
        while (running) {
            var rec: MediaRecorder? = null
            try {
                notify("Listening…")
                rec = Audio.startRecording(clip)
                Thread.sleep(recordMs)
                Audio.stopRecording(rec)
                rec = null
                if (!running) break

                val you = Bridge.stt(File(clip).readBytes())
                if (isNoise(you)) {
                    if (++idleWindows >= maxIdleWindows) {
                        notify("Idle — stopped")
                        selfStop()
                        break
                    }
                    continue
                }
                idleWindows = 0
                notify("Heard: ${you.take(40)}")

                val reply = Bridge.chat(you)
                notify("Speaking…")
                val audio = Bridge.tts(reply)
                File(replyPath).writeBytes(audio)
                Audio.playSync(replyPath)
            } catch (_: InterruptedException) {
                break
            } catch (e: Exception) {
                notify("err: ${e.message?.take(40)}")
                try {
                    Thread.sleep(1500)
                } catch (_: InterruptedException) {
                    break
                }
            } finally {
                if (rec != null) Audio.stopRecording(rec)
            }
        }
    }

    /** Skip empty transcripts and whisper's non-speech markers like [BLANK_AUDIO] / (silence). */
    private fun isNoise(t: String): Boolean {
        val s = t.trim()
        return s.length < 2 || s.startsWith("[") || s.startsWith("(")
    }

    /** External stop (notification / in-app toggle): also interrupts the loop thread. */
    private fun stopEverything() {
        running = false
        loopThread?.interrupt()
        loopThread = null
        releaseWakeLock()
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    /** Internal stop from inside the loop thread (idle auto-stop); no self-interrupt. */
    private fun selfStop() {
        running = false
        releaseWakeLock()
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun releaseWakeLock() {
        try {
            wakeLock?.let { if (it.isHeld) it.release() }
        } catch (_: Exception) {
        }
        wakeLock = null
    }

    override fun onDestroy() {
        running = false
        releaseWakeLock()
        super.onDestroy()
    }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val ch = NotificationChannel(
                CHANNEL_ID, "Xorics voice", NotificationManager.IMPORTANCE_LOW
            ).apply { description = "Background listening" }
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
            this, 1, Intent(this, VoiceService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Xorics")
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
