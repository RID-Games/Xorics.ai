package com.rid.xorics.g2

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pure-JVM tests for the G2 protocol layer — no device, runs on `./gradlew test`.
 * The expected auth-packet hex is the frozen oracle proven byte-for-byte against
 * the working teleprompter.py reference (see GlassesProtocol KDoc).
 */
class GlassesProtocolTest {

    private fun hex(b: ByteArray) = GlassesProtocol.toHex(b)

    @Test fun crcKnownAnswer() {
        assertEquals(0x29B1, GlassesProtocol.crc16("123456789".toByteArray(Charsets.US_ASCII)))
    }

    @Test fun varintVectors() {
        assertEquals("0a", hex(GlassesProtocol.encodeVarint(10)))
        assertEquals("7f", hex(GlassesProtocol.encodeVarint(127)))
        assertEquals("8001", hex(GlassesProtocol.encodeVarint(128)))
        assertEquals("ff01", hex(GlassesProtocol.encodeVarint(255)))
        assertEquals("ac02", hex(GlassesProtocol.encodeVarint(300)))
        assertEquals("00", hex(GlassesProtocol.encodeVarint(0)))
    }

    @Test fun authPacketsMatchOracle() {
        val expected = listOf(
            "aa21010c010180000804100c1a0408011004c6bc",
            "aa21020a010180200805100e22020802c29e",
            "aa21031b01018020088001100f82081108808c90c30610e8ffffffffffffffff01916d",
            "aa21040c01018000080410101a0408011004d6d9",
            "aa21050c01018000080410111a0408011004b761",
            "aa21060a010180200805101222020801d021",
            "aa21071b01018020088001101382081108808c90c30610e8ffffffffffffffff01914b",
        )
        val got = GlassesProtocol.buildAuthPackets(1751385600L)
        assertEquals(expected.size, got.size)
        for (i in expected.indices) assertEquals("auth packet ${i + 1}", expected[i], hex(got[i]))
    }

    @Test fun everyAuthPacketIsSelfConsistent() {
        for (p in GlassesProtocol.buildAuthPackets(1751385600L)) {
            assertEquals(0xAA, p[0].toInt() and 0xFF)
            assertEquals(0x21, p[1].toInt() and 0xFF)
            val payloadLen = (p[3].toInt() and 0xFF) - 2
            assertEquals(8 + payloadLen + 2, p.size)              // length byte consistent
            assertNotNull(GlassesProtocol.parseFrame(p))          // CRC validates
        }
    }

    @Test fun parseFrameRoundTrips() {
        val p = GlassesProtocol.buildAuthPackets(1751385600L)[2] // time-sync packet
        val f = GlassesProtocol.parseFrame(p)
        assertNotNull(f)
        assertEquals(0x80, f!!.serviceHi)
        assertEquals(0x20, f.serviceLo)
        assertEquals(0x03, f.seq)
    }

    @Test fun parseFrameRejectsBadCrc() {
        val p = GlassesProtocol.buildAuthPackets(1751385600L)[0].copyOf()
        p[p.size - 1] = (p[p.size - 1] + 1).toByte()             // corrupt CRC high byte
        assertNull(GlassesProtocol.parseFrame(p))
    }

    @Test fun parseFrameRejectsBadMagic() {
        val p = GlassesProtocol.buildAuthPackets(1751385600L)[0].copyOf()
        p[0] = 0x00
        assertNull(GlassesProtocol.parseFrame(p))
    }

    @Test fun armAndG2Detection() {
        assertTrue(GlassesProtocol.isG2("Even G2_11_L_A1B2C3"))
        assertEquals(GlassesProtocol.Arm.LEFT, GlassesProtocol.armOf("Even G2_11_L_A1B2C3"))
        assertEquals(GlassesProtocol.Arm.RIGHT, GlassesProtocol.armOf("Even G2_11_R_A1B2C3"))
        assertNull(GlassesProtocol.armOf("Some Other Device"))
    }

    @Test fun selfTestPasses() {
        assertEquals("OK", GlassesProtocol.selfTest())
    }
}
