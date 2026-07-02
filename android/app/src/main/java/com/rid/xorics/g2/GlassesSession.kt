package com.rid.xorics.g2

import android.content.Context
import android.os.Handler
import android.os.Looper
import java.util.concurrent.CopyOnWriteArrayList

/**
 * GlassesSession — the base system's core: one authenticated conversation with
 * one G2 arm, and the seam where glasses-side plugins ("features") attach.
 *
 * Layering (verification status in brackets):
 *   GlassesProtocol [proven]  →  GlassesText [proven]  →  GlassesLink [hardware-gated]
 *                                        ↑ this file orchestrates them [hardware-gated]
 *
 * Lifecycle mirrors the WORKING reference exactly: connect → notify on → 7 auth
 * packets → settle 500 ms → ready → display traffic. The reference never inspects
 * the auth ACK — it just waits and proceeds, and that works — so READY here is
 * time-based too. Every inbound frame is still parsed, kept in [lastFrames], and
 * surfaced, so Milestone 1 (first valid 0x12 frame) is captured without blocking
 * the pipeline on interpreting it.
 *
 * Counters: auth burns seq 1–7 / msgIds through 0x13, so this session continues
 * at 0x08 / 0x14 like the reference, advancing one per packet thereafter.
 *
 * Features are the plugin unit ON the phone. A feature declares which service
 * IDs it consumes (empty set = every frame), gets each parsed inbound frame, and
 * can send through [sendRaw] / the typed builders using [takeSeq]/[takeMsgId].
 * The Xorics-side (Python) plugins reach this through the bridge bus — see
 * GlassesBridgeClient / glasses_bus.py.
 */
class GlassesSession(
    context: Context,
    val arm: GlassesProtocol.Arm = GlassesProtocol.Arm.LEFT,
    private val callback: Callback,
) {

    interface Callback {
        fun onState(message: String)
        /** Auth sent + settle elapsed. Display traffic is now allowed. */
        fun onReady()
        /** Every 0x5402 notification; frame is non-null when magic+CRC validate. */
        fun onFrame(frame: GlassesProtocol.Frame?, raw: ByteArray)
        fun onError(message: String)
        /** A queued/played show-text script finished writing all packets. */
        fun onScriptDone() {}
    }

    /** A glasses-side plugin. Register with [addFeature]. */
    interface Feature {
        val name: String
        /** Service IDs this feature wants. Empty = receive every frame. */
        val services: Set<GlassesProtocol.Service> get() = emptySet()
        fun onReady(session: GlassesSession) {}
        fun onFrame(session: GlassesSession, frame: GlassesProtocol.Frame) {}
    }

    enum class State { IDLE, CONNECTING, AUTHENTICATING, READY, FAILED, CLOSED }

    @Volatile var state: State = State.IDLE
        private set

    private val handler = Handler(Looper.getMainLooper())
    private val features = CopyOnWriteArrayList<Feature>()

    /** Ring of the most recent raw 0x5402 notifications — the M1 evidence. */
    val lastFrames = ArrayDeque<ByteArray>()
    private val maxKeptFrames = 32

    private var nextSeq = 0x08
    private var nextMsgId = 0x14
    private var playing = false
    private var pendingText: String? = null

    private val link = GlassesLink(context, arm, object : GlassesLink.Listener {
        override fun onState(message: String) = callback.onState(message)

        override fun onAuthPacketsSent() {
            state = State.AUTHENTICATING
            callback.onState("Auth sent — settling ${GlassesText.AUTH_SETTLE_MS} ms (reference behavior)")
            handler.postDelayed({
                if (state == State.AUTHENTICATING) {
                    state = State.READY
                    callback.onReady()
                    for (f in features) runCatching { f.onReady(this@GlassesSession) }
                    pendingText?.let { text -> pendingText = null; play(text) }
                }
            }, GlassesText.AUTH_SETTLE_MS)
        }

        override fun onGlassesFrame(frame: GlassesProtocol.Frame?, raw: ByteArray) {
            synchronized(lastFrames) {
                lastFrames.addLast(raw.copyOf())
                while (lastFrames.size > maxKeptFrames) lastFrames.removeFirst()
            }
            if (frame != null) {
                for (f in features) {
                    if (f.services.isEmpty() || f.services.any { frame.serviceMatches(it) }) {
                        runCatching { f.onFrame(this@GlassesSession, frame) }
                    }
                }
            }
            callback.onFrame(frame, raw)
        }

        override fun onError(message: String) {
            state = State.FAILED
            callback.onError(message)
        }
    })

    // ── Lifecycle ────────────────────────────────────────────────────────────

    fun start() {
        check(state == State.IDLE) { "session already started (state=$state)" }
        state = State.CONNECTING
        link.start()
    }

    fun close() {
        state = State.CLOSED
        handler.removeCallbacksAndMessages(null)
        link.close()
    }

    fun addFeature(feature: Feature) {
        features.add(feature)
        if (state == State.READY) runCatching { feature.onReady(this) }
    }

    fun removeFeature(feature: Feature) { features.remove(feature) }

    // ── The one high-level verb v0 ships: text on the lens ──────────────────

    /**
     * Show [text] on the glasses using the proven config→init→pages→marker→sync
     * script. If the session isn't READY yet (or a script is mid-flight), the
     * text is queued (latest wins) and plays when possible. Returns true if it
     * started playing immediately, false if queued.
     */
    fun showText(text: String): Boolean {
        if (state == State.READY && !playing) { play(text); return true }
        pendingText = text
        callback.onState("showText queued (state=$state, playing=$playing)")
        return false
    }

    private fun play(text: String) {
        val script = try {
            GlassesText.buildShowTextScript(text, nextSeq, nextMsgId)
        } catch (e: IllegalArgumentException) {
            callback.onError("script build failed: ${e.message}"); return
        }
        nextSeq = script.nextSeq and 0xFF
        nextMsgId = script.nextMsgId
        playing = true
        callback.onState("Playing show-text script: ${script.steps.size} packets")
        var i = 0
        fun step() {
            if (state == State.CLOSED) { playing = false; return }
            if (i >= script.steps.size) {
                playing = false
                callback.onState("Script complete")
                callback.onScriptDone()
                pendingText?.let { t -> pendingText = null; play(t) }
                return
            }
            val s = script.steps[i]
            if (!link.write(s.packet)) {
                playing = false
                callback.onError("write failed at script packet ${i + 1}/${script.steps.size}")
                return
            }
            i++
            handler.postDelayed(::step, s.delayAfterMs.toLong())
        }
        step()
    }

    // ── Feature toolkit ──────────────────────────────────────────────────────

    /** Send one prebuilt packet (features build via GlassesProtocol/GlassesText). */
    fun sendRaw(packet: ByteArray): Boolean = state == State.READY && link.write(packet)

    /** Claim the next transport sequence number (one per packet). */
    fun takeSeq(): Int { val s = nextSeq; nextSeq = (nextSeq + 1) and 0xFF; return s }

    /** Claim the next protobuf message id (one per packet). */
    fun takeMsgId(): Int = nextMsgId++

    /** Recent raw notifications, newest last (Milestone-1 evidence). */
    fun recentFramesHex(): List<String> = synchronized(lastFrames) {
        lastFrames.map { GlassesProtocol.toHex(it) }
    }
}
