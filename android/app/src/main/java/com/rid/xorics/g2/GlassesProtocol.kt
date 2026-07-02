package com.rid.xorics.g2

/**
 * GlassesProtocol — Even Realities G2 wire protocol (pure logic, NO BLE, NO Android).
 *
 * This file contains ONLY framing / CRC / varint / packet construction / parsing.
 * It has zero Android or BLE imports so it runs under plain-JVM unit tests
 * (`src/test`) and can be verified without hardware. The BLE transport
 * (GlassesLink.kt) depends on this; this depends on nothing.
 *
 * ── PROVENANCE (verify, don't trust) ────────────────────────────────────────
 * Every byte below was cross-checked against ground truth, not memory:
 *   • Source of truth: i-soxi/even-g2-protocol — docs/ + examples/teleprompter/
 *     teleprompter.py (a WORKING connect+auth+display reference).
 *   • CRC is CRC-16/CCITT-FALSE. Known-answer test: crc("123456789") == 0x29B1.
 *   • The 7 auth packets produced by [buildAuthPackets] were proven byte-for-byte
 *     identical across THREE independent implementations at ts=1751385600:
 *       (1) teleprompter.py (the proven reference),
 *       (2) an independent Python reimplementation from the spec,
 *       (3) a JVM implementation using the SAME signed-byte handling as this file
 *           (masking with `and 0xFF`) — the one real Kotlin porting hazard.
 *   • [selfTest] re-verifies the KAT, varint vectors, and the full 7-packet oracle
 *     at RUNTIME, so a build regression fails loudly. Call it from a debug button.
 *
 * Kotlin note: `Byte` is signed (0xAA.toByte() == -86). All widening for CRC and
 * length math masks with `and 0xFF`; that is the bug this layer is careful about.
 */
object GlassesProtocol {

    // ── Transport constants ────────────────────────────────────────────────
    const val MAGIC = 0xAA            // header[0], every packet
    const val TYPE_COMMAND = 0x21     // header[1], phone -> glasses
    const val TYPE_RESPONSE = 0x12    // header[1], glasses -> phone

    /** Fixed 10-byte transaction id used in the time-sync auth packets. */
    private val TXID = intBytes(0xE8, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x01)

    // ── BLE identifiers (strings here so this file stays Android-free) ──────
    private const val UUID_BASE = "00002760-08c2-11e1-9073-0e8ac72e%04x"
    val UUID_SERVICE: String        = uuid(0x0000)   // main service
    val UUID_WRITE: String          = uuid(0x5401)   // Write Without Response, phone -> glasses
    val UUID_NOTIFY: String         = uuid(0x5402)   // Notify, glasses -> phone
    val UUID_DISPLAY: String        = uuid(0x6402)   // Display/rendering, 204-byte packets
    val UUID_SERVICE_DECL: String   = uuid(0x5450)

    /** Standard Client Characteristic Configuration Descriptor + its "enable notifications" value. */
    const val CCCD_UUID = "00002902-0000-1000-8000-00805f9b34fb"
    val CCCD_ENABLE_NOTIFICATION: ByteArray = intBytes(0x01, 0x00)

    /** Preferred ATT MTU. G2 supports up to 512; request 517 (512 + 5 ATT overhead) on Android. */
    const val PREFERRED_MTU = 517

    private fun uuid(suffix: Int): String = UUID_BASE.format(suffix)

    // ── Dual peripheral awareness ───────────────────────────────────────────
    /** G2 advertises two peripherals, e.g. "Even G2_11_L_A1B2C3" / "..._R_...". */
    enum class Arm(val token: String) { LEFT("_L_"), RIGHT("_R_") }

    fun isG2(name: String?): Boolean = name != null && name.contains("G2")

    fun armOf(name: String?): Arm? = when {
        name == null -> null
        name.contains(Arm.LEFT.token) -> Arm.LEFT
        name.contains(Arm.RIGHT.token) -> Arm.RIGHT
        else -> null
    }

    // ── Service IDs (header bytes 6–7) ──────────────────────────────────────
    // Low-byte convention: 0x00 control/query, 0x01 response, 0x20 data/payload.
    enum class Service(val hi: Int, val lo: Int) {
        AUTH_CONTROL(0x80, 0x00),
        AUTH_DATA(0x80, 0x20),
        AUTH_RESPONSE(0x80, 0x01),
        DISPLAY_WAKE(0x04, 0x20),
        TELEPROMPTER(0x06, 0x20),
        DASHBOARD(0x07, 0x20),
        DEVICE_INFO(0x09, 0x00),
        CONVERSATE(0x0B, 0x20),        // on-device STT transcription lands here
        CONVERSATE_ALT(0x11, 0x20),
        TASKS(0x0C, 0x20),
        CONFIG(0x0D, 0x00),
        DISPLAY_CONFIG(0x0E, 0x20),
        COMMIT(0x20, 0x20),
        DISPLAY_TRIGGER(0x81, 0x20);
    }

    // ── CRC-16/CCITT-FALSE ──────────────────────────────────────────────────
    // init 0xFFFF, poly 0x1021, no reflection, no final XOR, over payload only.
    fun crc16(data: ByteArray): Int {
        var crc = 0xFFFF
        for (b in data) {
            crc = crc xor ((b.toInt() and 0xFF) shl 8)      // mask: the porting hazard
            repeat(8) {
                crc = if (crc and 0x8000 != 0) ((crc shl 1) xor 0x1021) and 0xFFFF
                      else (crc shl 1) and 0xFFFF
            }
        }
        return crc
    }

    // ── Protobuf-style varint ───────────────────────────────────────────────
    fun encodeVarint(value: Long): ByteArray {
        require(value >= 0) { "varint requires a non-negative value, got $value" }
        val out = ArrayList<Byte>(10)
        var v = value
        while (v > 0x7FL) {
            out.add(((v and 0x7FL) or 0x80L).toByte())
            v = v ushr 7
        }
        out.add((v and 0x7FL).toByte())
        return out.toByteArray()
    }

    // ── Framing ─────────────────────────────────────────────────────────────
    // [AA][type][seq][len=payload+2][pktTotal][pktSerial][svcHi][svcLo][payload][crcLo][crcHi]
    // Single-packet only (auth never exceeds one packet). Multi-packet fragmentation
    // (PktTotal/PktSerial index the fragments, Seq stays constant) is a later concern.
    fun buildPacket(seq: Int, serviceHi: Int, serviceLo: Int, payload: ByteArray): ByteArray {
        val length = payload.size + 2
        require(length in 0..0xFF) {
            "payload ${payload.size}B overflows the single-byte length field; " +
                "multi-packet fragmentation not implemented (not needed for auth)"
        }
        val crc = crc16(payload)
        return intBytes(MAGIC, TYPE_COMMAND, seq and 0xFF, length, 0x01, 0x01, serviceHi, serviceLo) +
            payload +
            intBytes(crc and 0xFF, (crc ushr 8) and 0xFF)
    }

    fun buildPacket(seq: Int, service: Service, payload: ByteArray): ByteArray =
        buildPacket(seq, service.hi, service.lo, payload)

    // ── Auth payload builders (structure verified against teleprompter.py) ──
    private fun capabilityQuery(msgId: Int): ByteArray =
        intBytes(0x08, 0x04, 0x10) + encodeVarint(msgId.toLong()) + intBytes(0x1A, 0x04, 0x08, 0x01, 0x10, 0x04)

    private fun capabilityResponseReq(msgId: Int, value: Int): ByteArray =
        intBytes(0x08, 0x05, 0x10) + encodeVarint(msgId.toLong()) + intBytes(0x22, 0x02, 0x08, value)

    private fun timeSync(msgId: Int, epochSeconds: Long): ByteArray {
        // Nested TimeSyncData length is COMPUTED, not the hardcoded 0x11 the reference used —
        // proven equivalent for a 5-byte timestamp, and robust across timestamp widths.
        val nested = intBytes(0x08) + encodeVarint(epochSeconds) + intBytes(0x10) + TXID
        return intBytes(0x08, 0x80, 0x01, 0x10) + encodeVarint(msgId.toLong()) +
            intBytes(0x82, 0x08, nested.size) + nested
    }

    /**
     * The 7-packet authentication handshake — the Milestone-1 target.
     * Write these in order to [UUID_WRITE] with write-WITHOUT-response, ~100ms apart,
     * after notifications are enabled on [UUID_NOTIFY]. No BLE pairing/bonding, no PIN.
     *
     * @param epochSeconds usually System.currentTimeMillis() / 1000.
     */
    fun buildAuthPackets(epochSeconds: Long): List<ByteArray> = listOf(
        buildPacket(0x01, Service.AUTH_CONTROL, capabilityQuery(0x0C)),        // 1 capability query
        buildPacket(0x02, Service.AUTH_DATA,    capabilityResponseReq(0x0E, 0x02)), // 2 cap response req
        buildPacket(0x03, Service.AUTH_DATA,    timeSync(0x0F, epochSeconds)), // 3 time sync
        buildPacket(0x04, Service.AUTH_CONTROL, capabilityQuery(0x10)),        // 4 capability query
        buildPacket(0x05, Service.AUTH_CONTROL, capabilityQuery(0x11)),        // 5 capability query
        buildPacket(0x06, Service.AUTH_DATA,    capabilityResponseReq(0x12, 0x01)), // 6 cap response req
        buildPacket(0x07, Service.AUTH_DATA,    timeSync(0x13, epochSeconds)), // 7 final time sync
    )

    // ── Inbound frame parsing (glasses -> phone, type 0x12) ─────────────────
    data class Frame(
        val type: Int,
        val seq: Int,
        val packetTotal: Int,
        val packetSerial: Int,
        val serviceHi: Int,
        val serviceLo: Int,
        val payload: ByteArray,
    ) {
        val isResponse: Boolean get() = type == TYPE_RESPONSE
        fun serviceMatches(s: Service): Boolean = serviceHi == s.hi && serviceLo == s.lo

        // data class with ByteArray: hand-roll equals/hashCode for correctness.
        override fun equals(other: Any?): Boolean {
            if (this === other) return true
            if (other !is Frame) return false
            return type == other.type && seq == other.seq &&
                packetTotal == other.packetTotal && packetSerial == other.packetSerial &&
                serviceHi == other.serviceHi && serviceLo == other.serviceLo &&
                payload.contentEquals(other.payload)
        }
        override fun hashCode(): Int {
            var r = type
            r = 31 * r + seq; r = 31 * r + packetTotal; r = 31 * r + packetSerial
            r = 31 * r + serviceHi; r = 31 * r + serviceLo; r = 31 * r + payload.contentHashCode()
            return r
        }
    }

    /**
     * Parse one inbound frame. Returns null if the buffer is too short, the magic
     * is wrong, or the CRC does not validate. Does NOT interpret meaning — the
     * "authenticated" predicate must be learned from the live ACK (see HANDOFF).
     */
    fun parseFrame(data: ByteArray): Frame? {
        if (data.size < 10) return null
        if ((data[0].toInt() and 0xFF) != MAGIC) return null
        val length = data[3].toInt() and 0xFF          // = payloadLen + 2 (includes CRC)
        val payloadLen = length - 2
        if (payloadLen < 0) return null
        val total = 8 + length                          // 8 header + payload + 2 crc
        if (data.size < total) return null
        val payload = data.copyOfRange(8, 8 + payloadLen)
        val crcStored = (data[8 + payloadLen].toInt() and 0xFF) or
            ((data[9 + payloadLen].toInt() and 0xFF) shl 8)
        if (crcStored != crc16(payload)) return null
        return Frame(
            type = data[1].toInt() and 0xFF,
            seq = data[2].toInt() and 0xFF,
            packetTotal = data[4].toInt() and 0xFF,
            packetSerial = data[5].toInt() and 0xFF,
            serviceHi = data[6].toInt() and 0xFF,
            serviceLo = data[7].toInt() and 0xFF,
            payload = payload,
        )
    }

    // ── Utilities ───────────────────────────────────────────────────────────
    fun toHex(data: ByteArray): String =
        buildString(data.size * 2) { for (b in data) append("%02x".format(b.toInt() and 0xFF)) }

    private fun intBytes(vararg ints: Int): ByteArray = ByteArray(ints.size) { ints[it].toByte() }

    // ── Runtime self-verification (call from a debug button; returns "OK" or a reason) ──
    fun selfTest(): String {
        if (crc16("123456789".toByteArray(Charsets.US_ASCII)) != 0x29B1) return "FAIL: CRC KAT"
        val vectors = listOf(10L to "0a", 127L to "7f", 128L to "8001", 255L to "ff01", 300L to "ac02", 0L to "00")
        for ((v, h) in vectors) if (toHex(encodeVarint(v)) != h) return "FAIL: varint($v)"
        // Frozen oracle: the 7 auth packets at ts=1751385600 (proven three ways).
        val expected = listOf(
            "aa21010c010180000804100c1a0408011004c6bc",
            "aa21020a010180200805100e22020802c29e",
            "aa21031b01018020088001100f82081108808c90c30610e8ffffffffffffffff01916d",
            "aa21040c01018000080410101a0408011004d6d9",
            "aa21050c01018000080410111a0408011004b761",
            "aa21060a010180200805101222020801d021",
            "aa21071b01018020088001101382081108808c90c30610e8ffffffffffffffff01914b",
        )
        val got = buildAuthPackets(1751385600L)
        if (got.size != expected.size) return "FAIL: auth count ${got.size}"
        for (i in got.indices) {
            val h = toHex(got[i])
            if (h != expected[i]) return "FAIL: auth packet ${i + 1} = $h"
        }
        // Round-trip: a built packet must parse back with a valid CRC.
        val rt = parseFrame(got[2]) ?: return "FAIL: parseFrame(auth3) null"
        if (rt.serviceHi != 0x80 || rt.serviceLo != 0x20) return "FAIL: parseFrame service"
        // Corruption must be rejected.
        val bad = got[0].copyOf().also { it[it.size - 1] = (it[it.size - 1] + 1).toByte() }
        if (parseFrame(bad) != null) return "FAIL: bad CRC not rejected"
        return "OK"
    }
}
