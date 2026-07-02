package com.rid.xorics.g2

import android.annotation.SuppressLint
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.os.Build
import android.os.Handler
import android.os.Looper
import java.util.UUID

/**
 * GlassesLink — Milestone-1 BLE transport for one G2 arm.
 *
 * ⚠️ UNVERIFIED OFFLINE. Unlike GlassesProtocol (proven byte-for-byte three ways),
 * nothing in this file can be checked without the physical glasses — this is the
 * transport, and the transport is the only unknown left. It is deliberately a
 * near-literal port of the WORKING teleprompter.py flow:
 *
 *     scan "Even G2_..._L/R" -> connect -> (MTU) -> discover
 *     -> enable notify on 0x5402 (CCCD 0x0100)
 *     -> write the 7 auth packets to 0x5401, write-WITHOUT-response, ~100ms apart
 *     -> observe the glasses' 0x12 notification  == MILESTONE 1
 *
 * No pairing, no bonding, no PIN. Malformed input cannot brick the glasses — the
 * only brick vector is DFU firmware flashing, which this does not touch.
 *
 * Milestone 1 = [Listener.onGlassesFrame] fires with a valid parsed frame after
 * the 7 packets go out. The exact "authenticated" payload has to be read from that
 * live notification; this layer just surfaces it.
 *
 * Connects to ONE arm (default LEFT), exactly like the reference. Dual-arm is the
 * immediate follow-up: run two GlassesLink instances once single-arm ACK is confirmed.
 *
 * PERMISSIONS the caller must already hold (request in the Activity BEFORE start()):
 *   API 31+ : BLUETOOTH_SCAN (usesPermissionFlags neverForLocation), BLUETOOTH_CONNECT
 *   API <31 : BLUETOOTH, BLUETOOTH_ADMIN, ACCESS_FINE_LOCATION (+ location services ON)
 * The Fold5 is API 34 → SCAN + CONNECT runtime grants.
 */
@SuppressLint("MissingPermission") // caller guarantees the grants above before start()
class GlassesLink(
    private val context: Context,
    private val arm: GlassesProtocol.Arm = GlassesProtocol.Arm.LEFT,
    private val listener: Listener,
) {

    interface Listener {
        /** Human-readable lifecycle breadcrumbs for logcat / a debug TextView. */
        fun onState(message: String)
        /** All 7 auth packets have been written to 0x5401 (sent, not yet acked). */
        fun onAuthPacketsSent()
        /** A notification arrived on 0x5402. Non-null frame = valid magic + CRC. THIS is the ACK. */
        fun onGlassesFrame(frame: GlassesProtocol.Frame?, raw: ByteArray)
        /** Terminal failure; the link is torn down. */
        fun onError(message: String)
    }

    private val handler = Handler(Looper.getMainLooper())
    private val serviceUuid = UUID.fromString(GlassesProtocol.UUID_SERVICE)
    private val writeUuid = UUID.fromString(GlassesProtocol.UUID_WRITE)
    private val notifyUuid = UUID.fromString(GlassesProtocol.UUID_NOTIFY)
    private val cccdUuid = UUID.fromString(GlassesProtocol.CCCD_UUID)

    private var gatt: BluetoothGatt? = null
    private var writeChar: BluetoothGattCharacteristic? = null
    private var scanning = false
    private var closed = false

    private val adapter by lazy {
        (context.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager)?.adapter
    }

    // ── Public API ──────────────────────────────────────────────────────────

    fun start(scanTimeoutMs: Long = 15_000) {
        val ad = adapter
        if (ad == null || !ad.isEnabled) { fail("Bluetooth is off or unavailable"); return }
        val scanner = ad.bluetoothLeScanner
        if (scanner == null) { fail("No BLE scanner"); return }

        listener.onState("Scanning for Even G2 (${arm.name}) …")
        scanning = true
        // No ScanFilter on name — G2 advertises its full name which we match in the callback.
        val settings = ScanSettings.Builder()
            .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
            .build()
        scanner.startScan(null, settings, scanCallback)

        handler.postDelayed({
            if (scanning) {
                stopScan()
                fail("Scan timed out — no ${arm.name} arm found. Is Even Hub uninstalled and the arm awake?")
            }
        }, scanTimeoutMs)
    }

    fun close() {
        closed = true
        stopScan()
        handler.removeCallbacksAndMessages(null)
        try { gatt?.disconnect() } catch (_: Exception) {}
        try { gatt?.close() } catch (_: Exception) {}
        gatt = null
        writeChar = null
    }

    /**
     * Write one prebuilt packet to 0x5401 (write-without-response). Used by the
     * session layer (GlassesSession) for everything after the auth handshake.
     * Returns false if the link isn't up yet or the stack rejects the write.
     */
    fun write(data: ByteArray): Boolean {
        val g = gatt ?: return false
        val ch = writeChar ?: return false
        return try { writeNoResponse(g, ch, data) } catch (_: Exception) { false }
    }

    // ── Scanning ────────────────────────────────────────────────────────────

    private val scanCallback = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult) {
            val name = result.device?.name ?: result.scanRecord?.deviceName
            if (!GlassesProtocol.isG2(name)) return
            if (GlassesProtocol.armOf(name) != arm) return
            listener.onState("Found $name — connecting …")
            stopScan()
            connect(result.device)
        }

        override fun onScanFailed(errorCode: Int) {
            if (!scanning) return
            scanning = false
            fail("BLE scan failed (code $errorCode)")
        }
    }

    private fun stopScan() {
        if (!scanning) return
        scanning = false
        try { adapter?.bluetoothLeScanner?.stopScan(scanCallback) } catch (_: Exception) {}
    }

    // ── Connect + GATT ──────────────────────────────────────────────────────

    private fun connect(device: BluetoothDevice) {
        // autoConnect=false + TRANSPORT_LE is the combination that avoids status 133 flakiness.
        gatt = device.connectGatt(context, false, gattCallback, BluetoothDevice.TRANSPORT_LE)
    }

    private val gattCallback = object : BluetoothGattCallback() {

        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
            if (status != BluetoothGatt.GATT_SUCCESS) {
                // 133 = generic GATT error; usual fix is retry or ensure Even Hub isn't holding the link.
                fail("Connection error (status $status). If 133: uninstall Even Hub so Xorics is sole owner, then retry.")
                return
            }
            when (newState) {
                BluetoothProfile.STATE_CONNECTED -> {
                    listener.onState("Connected — requesting MTU ${GlassesProtocol.PREFERRED_MTU} …")
                    if (!g.requestMtu(GlassesProtocol.PREFERRED_MTU)) {
                        // Some stacks refuse; fall straight through to discovery.
                        listener.onState("MTU request refused — discovering services …")
                        g.discoverServices()
                    }
                }
                BluetoothProfile.STATE_DISCONNECTED -> {
                    if (!closed) fail("Glasses disconnected")
                }
            }
        }

        override fun onMtuChanged(g: BluetoothGatt, mtu: Int, status: Int) {
            listener.onState("MTU = $mtu — discovering services …")
            g.discoverServices()
        }

        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
            if (status != BluetoothGatt.GATT_SUCCESS) { fail("Service discovery failed (status $status)"); return }
            val svc = g.getService(serviceUuid)
            if (svc == null) { fail("G2 service $serviceUuid not found"); return }
            val notify = svc.getCharacteristic(notifyUuid)
            val write = svc.getCharacteristic(writeUuid)
            if (notify == null || write == null) { fail("0x5401/0x5402 characteristics missing"); return }
            writeChar = write

            // Enable notifications on 0x5402: local flag + CCCD descriptor write (0x0100).
            g.setCharacteristicNotification(notify, true)
            val cccd = notify.getDescriptor(cccdUuid)
            if (cccd == null) { fail("CCCD descriptor missing on 0x5402"); return }
            listener.onState("Enabling notifications on 0x5402 …")
            writeCccd(g, cccd)
        }

        override fun onDescriptorWrite(g: BluetoothGatt, descriptor: BluetoothGattDescriptor, status: Int) {
            if (descriptor.uuid != cccdUuid) return
            if (status != BluetoothGatt.GATT_SUCCESS) { fail("CCCD write failed (status $status)"); return }
            listener.onState("Notifications enabled — sending 7 auth packets …")
            sendAuthSequence()
        }

        // API 33+ delivers bytes directly.
        override fun onCharacteristicChanged(
            g: BluetoothGatt, ch: BluetoothGattCharacteristic, value: ByteArray,
        ) { if (ch.uuid == notifyUuid) handleNotification(value) }

        // API < 33 delivers via ch.value.
        @Deprecated("Deprecated in API 33")
        override fun onCharacteristicChanged(g: BluetoothGatt, ch: BluetoothGattCharacteristic) {
            if (ch.uuid == notifyUuid) handleNotification(ch.value ?: ByteArray(0))
        }
    }

    // ── Auth send (7 packets, write-without-response, ~100ms apart) ──────────

    private fun sendAuthSequence() {
        val g = gatt ?: return
        val ch = writeChar ?: return
        val packets = GlassesProtocol.buildAuthPackets(System.currentTimeMillis() / 1000)
        var i = 0
        fun step() {
            if (closed) return
            if (i >= packets.size) { listener.onState("Auth packets sent — waiting for ACK on 0x5402"); listener.onAuthPacketsSent(); return }
            val ok = writeNoResponse(g, ch, packets[i])
            if (!ok) { fail("write of auth packet ${i + 1} failed"); return }
            i++
            handler.postDelayed(::step, 100)
        }
        step()
    }

    private fun handleNotification(value: ByteArray) {
        val frame = GlassesProtocol.parseFrame(value)
        listener.onState("◀ 0x5402 ${GlassesProtocol.toHex(value)}" + if (frame == null) " (unparsed)" else " (svc ${"%02x%02x".format(frame.serviceHi, frame.serviceLo)})")
        listener.onGlassesFrame(frame, value)
    }

    // ── API-level branching for the write calls ─────────────────────────────

    private fun writeNoResponse(g: BluetoothGatt, ch: BluetoothGattCharacteristic, data: ByteArray): Boolean {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            g.writeCharacteristic(ch, data, BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE) ==
                BluetoothGatt.GATT_SUCCESS
        } else {
            @Suppress("DEPRECATION")
            run {
                ch.writeType = BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE
                ch.value = data
                g.writeCharacteristic(ch)
            }
        }
    }

    private fun writeCccd(g: BluetoothGatt, cccd: BluetoothGattDescriptor) {
        val v = GlassesProtocol.CCCD_ENABLE_NOTIFICATION
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            g.writeDescriptor(cccd, v)
        } else {
            @Suppress("DEPRECATION")
            run { cccd.value = v; g.writeDescriptor(cccd) }
        }
    }

    private fun fail(message: String) {
        handler.post {
            listener.onError(message)
            close()
        }
    }
}

/*
 * ── Debug trigger (mirror the NavTestActivity pattern; remove before ship) ──
 *
 * class G2AuthTestActivity : ComponentActivity() {
 *   override fun onCreate(s: Bundle?) {
 *     super.onCreate(s)
 *     // 1) request BLUETOOTH_SCAN + BLUETOOTH_CONNECT first (ActivityResultContracts.RequestMultiplePermissions)
 *     // 2) then:
 *     GlassesLink(this, GlassesProtocol.Arm.LEFT, object : GlassesLink.Listener {
 *       override fun onState(m: String)        { Log.i("G2", m) }
 *       override fun onAuthPacketsSent()        { Log.i("G2", "7 packets sent") }
 *       override fun onGlassesFrame(f: GlassesProtocol.Frame?, raw: ByteArray) {
 *         Log.i("G2", "ACK type=${f?.type?.let { "%02x".format(it) }} raw=${GlassesProtocol.toHex(raw)}")  // ← MILESTONE 1
 *       }
 *       override fun onError(m: String)         { Log.e("G2", m) }
 *     }).start()
 *   }
 * }
 *
 * Sanity first, no glasses needed:  Log.i("G2", GlassesProtocol.selfTest())  // must print "OK"
 */
