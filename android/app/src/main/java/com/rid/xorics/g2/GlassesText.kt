package com.rid.xorics.g2

import java.security.MessageDigest

/**
 * GlassesText — put text on the G2 lens (pure logic, NO BLE, NO Android).
 *
 * The display path is: display-config (0x0E20) → teleprompter init (0x0620/1)
 * → 10 content pages → marker (0x0620/255) → pages 10–11 → sync (0x8000/14)
 * → remaining pages. [buildShowTextScript] captures that exact order AND the
 * reference's inter-packet timing, so the transport just plays it.
 *
 * ── PROVENANCE ──────────────────────────────────────────────────────────────
 * Verified the same way as GlassesProtocol's auth sequence:
 *   • Reference: teleprompter.py from i-soxi/even-g2-protocol (working code).
 *   • An independent Python reimplementation matched the reference byte-for-byte
 *     across a battery: every builder (incl. 2-byte varint msgIds, unicode text,
 *     total_lines 1..300), format_text over an 8-text corpus, and the FULL
 *     18-packet canonical script with identical delays.
 *   • A JVM implementation (same signed-byte, UTF-8, and integer-division
 *     semantics as this file) reproduced the canonical script exactly:
 *     SHA-256 d9b5114410540327bc14955e47b2950750a9aec473047270c1b7a301417f91f5.
 *   • The 106-byte display-region config blob below was extracted MECHANICALLY
 *     from a reference-built packet — never hand-copied.
 *   • [selfTest] re-verifies the script digest + spot packets at runtime.
 *
 * Known limit (inherited from the reference): a maximally dense page (10 full
 * 25-char lines) can push a content-page payload past the single-byte length
 * field. GlassesProtocol.buildPacket throws loudly in that case; multi-packet
 * fragmentation is a later layer. Normal text never hits it.
 */
object GlassesText {

    /** Reference timing (ms to wait AFTER each write), traced from send_text. */
    const val DELAY_AFTER_CONFIG_MS = 300
    const val DELAY_AFTER_INIT_MS = 500
    const val DELAY_AFTER_PAGE_MS = 100
    /** Reference sleeps 0.5s between the auth burst and the first display packet. */
    const val AUTH_SETTLE_MS = 500L

    /** Fixed display-region config (106 bytes), byte-identical to the reference. */
    private val CONFIG_BLOB: ByteArray = hexToBytes(
        "08011213080210904e1d00e0944425000000002800300012130803100d0f1d00408d4425000000002800" +
        "30001212080410001d000088422500000000280030001212080510001d00009242250000a24228003000" +
        "1212080610001d0000c642250000c442280030001800"
    )

    // ── Builders (each returns one complete framed packet) ──────────────────

    /** Service 0x0E-20: display configuration. Send first, once per session. */
    fun buildDisplayConfig(seq: Int, msgId: Int): ByteArray {
        val payload = ib(0x08, 0x02, 0x10) + v(msgId) + ib(0x22) + v(CONFIG_BLOB.size) + CONFIG_BLOB
        return GlassesProtocol.buildPacket(seq, GlassesProtocol.Service.DISPLAY_CONFIG, payload)
    }

    /** Service 0x06-20 type=1: initialize the teleprompter for [totalLines] of raw text. */
    fun buildTeleprompterInit(seq: Int, msgId: Int, totalLines: Int, manualMode: Boolean = true): ByteArray {
        val mode = if (manualMode) 0x00 else 0x01
        val contentHeight = maxOf(1L, totalLines.toLong() * 2665L / 140L)
        val display = ib(0x08, 0x01, 0x10, 0x00, 0x18, 0x00, 0x20, 0x8B, 0x02) +   // fixed settings, width=267
            ib(0x28) + v(contentHeight) +                                          // content height
            ib(0x30, 0xE6, 0x01) +                                                 // line height 230
            ib(0x38, 0x8E, 0x0A) +                                                 // viewport 1294
            ib(0x40, 0x05, 0x48, mode)                                             // font 5 + scroll mode
        val settings = ib(0x08, 0x01, 0x12) + v(display.size) + display
        val payload = ib(0x08, 0x01, 0x10) + v(msgId) + ib(0x1A) + v(settings.size) + settings
        return GlassesProtocol.buildPacket(seq, GlassesProtocol.Service.TELEPROMPTER, payload)
    }

    /** Service 0x06-20 type=3: one content page (the reference prefixes text with "\n"). */
    fun buildContentPage(seq: Int, msgId: Int, pageNumber: Int, text: String): ByteArray {
        val tb = ("\n" + text).toByteArray(Charsets.UTF_8)
        val inner = ib(0x08) + v(pageNumber) + ib(0x10, 0x0A) + ib(0x1A) + v(tb.size) + tb
        val content = ib(0x2A) + v(inner.size) + inner
        val payload = ib(0x08, 0x03, 0x10) + v(msgId) + content
        return GlassesProtocol.buildPacket(seq, GlassesProtocol.Service.TELEPROMPTER, payload)
    }

    /** Service 0x06-20 type=255: mid-stream marker (sent after page 9). */
    fun buildMarker(seq: Int, msgId: Int): ByteArray {
        val payload = ib(0x08, 0xFF, 0x01, 0x10) + v(msgId) + ib(0x6A, 0x04, 0x08, 0x00, 0x10, 0x06)
        return GlassesProtocol.buildPacket(seq, GlassesProtocol.Service.TELEPROMPTER, payload)
    }

    /** Service 0x80-00 type=14: sync/trigger (sent after page 11). */
    fun buildSync(seq: Int, msgId: Int): ByteArray {
        val payload = ib(0x08, 0x0E, 0x10) + v(msgId) + ib(0x6A, 0x00)
        return GlassesProtocol.buildPacket(seq, GlassesProtocol.Service.AUTH_CONTROL, payload)
    }

    // ── Pagination (exact port of the reference's format_text) ──────────────
    fun formatText(text: String, charsPerLine: Int = 25, linesPerPage: Int = 10): List<String> {
        val t = text.replace("\\n", "\n")
        val wrapped = ArrayList<String>()
        for (line in t.split("\n")) {
            if (line.trim().isEmpty()) { wrapped.add(""); continue }
            var current = ""
            for (word in line.trim().split(Regex("\\s+"))) {
                if (word.isEmpty()) continue
                if (current.length + word.length + 1 > charsPerLine) {
                    if (current.isNotEmpty()) wrapped.add(current.trim())
                    current = "$word "
                } else {
                    current += "$word "
                }
            }
            if (current.trim().isNotEmpty()) wrapped.add(current.trim())
        }
        if (wrapped.isEmpty()) wrapped.add(t)
        while (wrapped.size < linesPerPage) wrapped.add(" ")
        val pages = ArrayList<String>()
        var i = 0
        while (i < wrapped.size) {
            val pl = ArrayList(wrapped.subList(i, minOf(i + linesPerPage, wrapped.size)))
            while (pl.size < linesPerPage) pl.add(" ")
            pages.add(pl.joinToString("\n") + " \n")
            i += linesPerPage
        }
        while (pages.size < 14) pages.add(List(linesPerPage) { " " }.joinToString("\n") + " \n")
        return pages
    }

    // ── The full show-text script ────────────────────────────────────────────
    /** One write plus how long to wait after it before the next. */
    data class Step(val delayAfterMs: Int, val packet: ByteArray) {
        override fun equals(other: Any?) =
            other is Step && delayAfterMs == other.delayAfterMs && packet.contentEquals(other.packet)
        override fun hashCode() = 31 * delayAfterMs + packet.contentHashCode()
    }

    data class Script(val steps: List<Step>, val nextSeq: Int, val nextMsgId: Int)

    /**
     * Build the complete proven sequence for [text]. Auth uses seq 1–7 / msgIds
     * up to 0x13, so a fresh session continues at the reference's 0x08 / 0x14.
     */
    fun buildShowTextScript(text: String, seqStart: Int = 0x08, msgIdStart: Int = 0x14): Script {
        val pages = formatText(text)
        val totalLines = text.replace("\\n", "\n").split("\n").size
        var seq = seqStart
        var msg = msgIdStart
        val steps = ArrayList<Step>(pages.size + 4)
        fun emit(packet: ByteArray, delay: Int) { steps.add(Step(delay, packet)); seq++; msg++ }
        emit(buildDisplayConfig(seq, msg), DELAY_AFTER_CONFIG_MS)
        emit(buildTeleprompterInit(seq, msg, totalLines), DELAY_AFTER_INIT_MS)
        for (i in 0 until minOf(10, pages.size)) emit(buildContentPage(seq, msg, i, pages[i]), DELAY_AFTER_PAGE_MS)
        emit(buildMarker(seq, msg), DELAY_AFTER_PAGE_MS)
        for (i in 10 until minOf(12, pages.size)) emit(buildContentPage(seq, msg, i, pages[i]), DELAY_AFTER_PAGE_MS)
        emit(buildSync(seq, msg), DELAY_AFTER_PAGE_MS)
        for (i in 12 until pages.size) emit(buildContentPage(seq, msg, i, pages[i]), DELAY_AFTER_PAGE_MS)
        return Script(steps, seq, msg)
    }

    // ── Runtime self-verification against the frozen oracle ─────────────────
    fun selfTest(): String {
        val canon = "Hello from Xorics!\nBase system online."
        val script = buildShowTextScript(canon)
        if (script.steps.size != 18) return "FAIL: script packets ${script.steps.size}"
        val md = MessageDigest.getInstance("SHA-256")
        for (s in script.steps) md.update(s.packet)
        val digest = GlassesProtocol.toHex(md.digest())
        if (digest != "d9b5114410540327bc14955e47b2950750a9aec473047270c1b7a301417f91f5")
            return "FAIL: script sha256 $digest"
        if (GlassesProtocol.toHex(buildContentPage(10, 0x16, 0, "hello world")) !=
            "aa210a1a01010620080310162a120800100a1a0c0a68656c6c6f20776f726c649ed0")
            return "FAIL: contentPage oracle"
        if (GlassesProtocol.toHex(buildMarker(0x14, 0x20)) != "aa21140d0101062008ff0110206a04080010068e15")
            return "FAIL: marker oracle"
        if (GlassesProtocol.toHex(buildSync(0x15, 0x21)) != "aa21150801018000080e10216a004a82")
            return "FAIL: sync oracle"
        if (formatText(canon)[0] != "Hello from Xorics!\nBase system online.\n \n \n \n \n \n \n \n  \n")
            return "FAIL: formatText page0"
        for (s in script.steps) if (GlassesProtocol.parseFrame(s.packet) == null)
            return "FAIL: a script packet fails parse/CRC"
        return "OK"
    }

    // ── helpers ──────────────────────────────────────────────────────────────
    private fun ib(vararg ints: Int) = ByteArray(ints.size) { ints[it].toByte() }
    private fun v(value: Int) = GlassesProtocol.encodeVarint(value.toLong())
    private fun v(value: Long) = GlassesProtocol.encodeVarint(value)
    private fun hexToBytes(s: String) = ByteArray(s.length / 2) {
        ((Character.digit(s[2 * it], 16) shl 4) or Character.digit(s[2 * it + 1], 16)).toByte()
    }
}
