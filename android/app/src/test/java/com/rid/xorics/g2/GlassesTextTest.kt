package com.rid.xorics.g2

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.security.MessageDigest

/**
 * Pure-JVM tests for the display layer. EVERY expected literal in this file was
 * machine-generated from the implementation that was proven byte-for-byte against
 * the working teleprompter.py reference (see GlassesText KDoc) — none hand-typed.
 */
class GlassesTextTest {

    private fun hex(b: ByteArray) = GlassesProtocol.toHex(b)
    private val canon = "Hello from Xorics!\nBase system online."

    @Test fun displayConfigMatchesOracle() {
        val expected =
            "aa21087201010e2008021014226a08011213080210904e1d00e0944425000000002800300012130803100d0f" +
            "1d00408d442500000000280030001212080410001d000088422500000000280030001212080510001d000092" +
            "42250000a242280030001212080610001d0000c642250000c44228003000180032d1"
        assertEquals(expected, hex(GlassesText.buildDisplayConfig(8, 0x14)))
    }

    @Test fun tpInitMatchesOracle() {
        assertEquals(
            "aa21092101010620080110151a1908011215080110001800208b02282630e601388e0a40054800ff25",
            hex(GlassesText.buildTeleprompterInit(9, 0x15, totalLines = 2))
        )
    }

    @Test fun contentPageMatchesOracle() {
        assertEquals(
            "aa210a1a01010620080310162a120800100a1a0c0a68656c6c6f20776f726c649ed0",
            hex(GlassesText.buildContentPage(10, 0x16, 0, "hello world"))
        )
    }

    @Test fun markerMatchesOracle() {
        assertEquals("aa21140d0101062008ff0110206a04080010068e15", hex(GlassesText.buildMarker(0x14, 0x20)))
    }

    @Test fun syncMatchesOracle() {
        assertEquals("aa21150801018000080e10216a004a82", hex(GlassesText.buildSync(0x15, 0x21)))
    }

    @Test fun formatTextCanonicalPage0() {
        val pages = GlassesText.formatText(canon)
        assertEquals(14, pages.size)
        assertEquals("Hello from Xorics!\nBase system online.\n \n \n \n \n \n \n \n  \n", pages[0])
        assertEquals(List(10) { " " }.joinToString("\n") + " \n", pages[13])
    }

    @Test fun canonicalScriptMatchesFrozenDigest() {
        val script = GlassesText.buildShowTextScript(canon)
        assertEquals(18, script.steps.size)
        val md = MessageDigest.getInstance("SHA-256")
        for (s in script.steps) md.update(s.packet)
        assertEquals("d9b5114410540327bc14955e47b2950750a9aec473047270c1b7a301417f91f5", GlassesProtocol.toHex(md.digest()))
        assertEquals(300, script.steps[0].delayAfterMs)
        assertEquals(500, script.steps[1].delayAfterMs)
        assertTrue(script.steps.drop(2).all { it.delayAfterMs == 100 })
        assertEquals(0x08 + 18, script.nextSeq)
        assertEquals(0x14 + 18, script.nextMsgId)
        for (s in script.steps) assertNotNull(GlassesProtocol.parseFrame(s.packet))
    }

    @Test fun selfTestPasses() {
        assertEquals("OK", GlassesText.selfTest())
    }
}
