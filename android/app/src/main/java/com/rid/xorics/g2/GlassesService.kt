package com.rid.xorics.g2

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat

/**
 * GlassesService — foreground home of the base system on the phone.
 *
 * Owns exactly one GlassesSession (BLE link + auth + script playback) and one
 * GlassesBridgeClient (long-poll for plugin commands, event/frame reporting),
 * so the glasses link survives activity lifecycle. foregroundServiceType is
 * "connectedDevice" (declared in the manifest alongside the
 * FOREGROUND_SERVICE_CONNECTED_DEVICE permission).
 *
 * Every inbound BLE frame is logged under TAG and forwarded to the bridge, so
 * Milestone-1 evidence shows up BOTH in logcat and in
 *   curl {bridge}/glasses/events   on RIDGames.
 *
 * v0 is single-arm (default LEFT, override via EXTRA_ARM). Dual-arm is a
 * follow-up: two sessions behind one service.
 */
class GlassesService : Service() {

    private var session: GlassesSession? = null
    private var client: GlassesBridgeClient? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val ch = NotificationChannel(CHANNEL_ID, "Xorics G2 link", NotificationManager.IMPORTANCE_LOW)
        (getSystemService(NOTIFICATION_SERVICE) as NotificationManager).createNotificationChannel(ch)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (session != null) {
            Log.i(TAG, "service already running — ignoring duplicate start")
            return START_STICKY
        }
        val armName = intent?.getStringExtra(EXTRA_ARM) ?: "LEFT"
        val arm = runCatching { GlassesProtocol.Arm.valueOf(armName) }
            .getOrDefault(GlassesProtocol.Arm.LEFT)

        startInForeground(arm)

        val s = GlassesSession(this, arm, object : GlassesSession.Callback {
            override fun onState(message: String) {
                Log.i(TAG, message)
                client?.reportNote("state", message)
            }

            override fun onReady() {
                Log.i(TAG, "session READY ($arm)")
                client?.reportNote("ready", "session READY, arm=$arm")
            }

            override fun onFrame(frame: GlassesProtocol.Frame?, raw: ByteArray) {
                // Milestone-1 evidence: every 0x5402 notification, verbatim.
                Log.i(TAG, "frame<= " + GlassesProtocol.toHex(raw))
                client?.reportFrame(frame, raw)
            }

            override fun onError(message: String) {
                Log.e(TAG, message)
                client?.reportNote("error", message)
            }

            override fun onScriptDone() {
                client?.reportNote("script", "show-text script complete")
            }
        })
        session = s
        client = GlassesBridgeClient(s) { msg -> Log.i(TAG, msg) }
        client?.start()   // start polling first so early notes/frames are reported
        s.start()
        return START_STICKY
    }

    private fun startInForeground(arm: GlassesProtocol.Arm) {
        val notif: Notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Xorics G2")
            .setContentText("Glasses link — $arm arm")
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIF_ID, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
        } else {
            startForeground(NOTIF_ID, notif)
        }
    }

    override fun onDestroy() {
        client?.stop(); client = null
        session?.close(); session = null
        super.onDestroy()
    }

    companion object {
        private const val TAG = "XoricsG2"
        private const val CHANNEL_ID = "g2_link"
        private const val NOTIF_ID = 1002
        const val EXTRA_ARM = "arm"

        fun start(context: Context, arm: String = "LEFT") {
            context.startForegroundService(
                Intent(context, GlassesService::class.java).putExtra(EXTRA_ARM, arm)
            )
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, GlassesService::class.java))
        }
    }
}
