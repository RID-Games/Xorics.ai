package com.rid.xorics

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.media.MediaRecorder
import java.io.File

/**
 * Record/playback primitives, reused by both MainActivity and VoiceService.
 *
 * The optional [report] callback drives the on-screen audio diagnostic. It defaults
 * to a no-op, so production calls just omit it. (The MainActivity wiring that displays
 * it is commented out; pass a reporter again to re-enable debugging.)
 */
object Audio {

    /** Start recording mic audio to [path] (MPEG-4/AAC). Returns the recorder to stop later. */
    fun startRecording(path: String): MediaRecorder {
        // No-arg constructor is deprecated on API 31+ but works on all supported levels.
        val r = MediaRecorder()
        r.setAudioSource(MediaRecorder.AudioSource.MIC)
        r.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
        r.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
        r.setAudioSamplingRate(16000)
        r.setOutputFile(path)
        r.prepare()
        r.start()
        return r
    }

    fun stopRecording(r: MediaRecorder) {
        try {
            r.stop()
        } catch (_: Exception) {
            // stop() throws if nothing was recorded; ignore.
        } finally {
            r.release()
        }
    }

    /** Fire-and-forget playback (used by the foreground activity). */
    fun play(path: String, report: (String) -> Unit = {}) {
        Thread { playSync(path, report) }.start()
    }

    /** Blocking playback (used by the service loop so the next record waits for the reply). */
    fun playSync(path: String, report: (String) -> Unit = {}) {
        try {
            val bytes = File(path).readBytes()
            val head = if (bytes.size >= 4) String(bytes, 0, 4, Charsets.US_ASCII) else "?"
            report("tts ${bytes.size}B head=$head")

            val wav = parseWav(bytes)
            if (wav == null) {
                report("WAV parse failed")
                return
            }
            report("${wav.sampleRate}Hz ch${wav.channels} ${wav.bits}bit pcm=${wav.pcm.size}B")
            playPcm(wav, report)
        } catch (e: Exception) {
            report("audio err: ${e.message}")
        }
    }

    private fun playPcm(wav: Wav, report: (String) -> Unit) {
        val channelMask =
            if (wav.channels >= 2) AudioFormat.CHANNEL_OUT_STEREO else AudioFormat.CHANNEL_OUT_MONO
        val encoding = if (wav.bits == 8) AudioFormat.ENCODING_PCM_8BIT else AudioFormat.ENCODING_PCM_16BIT

        val minBuf = AudioTrack.getMinBufferSize(wav.sampleRate, channelMask, encoding)
        report("minBuf=$minBuf")
        if (minBuf <= 0) {
            report("unsupported audio config")
            return
        }
        val bufSize = maxOf(minBuf, 16384)

        val track = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setSampleRate(wav.sampleRate)
                    .setChannelMask(channelMask)
                    .setEncoding(encoding)
                    .build()
            )
            .setBufferSizeInBytes(bufSize)
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()

        report("track state=${track.state}")
        if (track.state != AudioTrack.STATE_INITIALIZED) {
            report("track init failed")
            track.release()
            return
        }

        try {
            track.play()

            var written = 0
            val chunk = 8192
            while (written < wav.pcm.size) {
                val n = track.write(wav.pcm, written, minOf(chunk, wav.pcm.size - written))
                if (n <= 0) {
                    report("write returned $n")
                    break
                }
                written += n
            }
            report("wrote ${written}B play=${track.playState}")

            // Wait for the buffered audio to finish so the tail isn't cut, with a timeout.
            val bytesPerFrame = (if (wav.bits == 8) 1 else 2) * maxOf(1, wav.channels)
            val totalFrames = wav.pcm.size / bytesPerFrame
            val durationMs = (totalFrames.toLong() * 1000 / maxOf(1, wav.sampleRate)) + 800
            val deadline = System.currentTimeMillis() + durationMs
            while (track.playState == AudioTrack.PLAYSTATE_PLAYING &&
                track.playbackHeadPosition < totalFrames &&
                System.currentTimeMillis() < deadline
            ) {
                Thread.sleep(20)
            }
            report("head=${track.playbackHeadPosition}/$totalFrames")
            track.stop()
        } catch (e: Exception) {
            report("play err: ${e.message}")
        } finally {
            track.release()
        }
    }

    private data class Wav(
        val sampleRate: Int,
        val channels: Int,
        val bits: Int,
        val pcm: ByteArray
    )

    /** Minimal WAV parser. Walks chunks (little-endian) and tolerates a 0/placeholder data size. */
    private fun parseWav(b: ByteArray): Wav? {
        if (b.size < 44) return null

        fun ascii(off: Int, len: Int) = String(b, off, len, Charsets.US_ASCII)
        fun u16(off: Int) = (b[off].toInt() and 0xFF) or ((b[off + 1].toInt() and 0xFF) shl 8)
        fun u32(off: Int) = (b[off].toInt() and 0xFF) or
            ((b[off + 1].toInt() and 0xFF) shl 8) or
            ((b[off + 2].toInt() and 0xFF) shl 16) or
            ((b[off + 3].toInt() and 0xFF) shl 24)

        if (ascii(0, 4) != "RIFF" || ascii(8, 4) != "WAVE") return null

        var pos = 12
        var sampleRate = 24000
        var channels = 1
        var bits = 16
        var dataOff = -1
        var dataLen = 0

        while (pos + 8 <= b.size) {
            val id = ascii(pos, 4)
            val size = u32(pos + 4)
            val body = pos + 8
            when (id) {
                "fmt " -> if (body + 16 <= b.size) {
                    channels = u16(body + 2)
                    sampleRate = u32(body + 4)
                    bits = u16(body + 14)
                }
                "data" -> {
                    dataOff = body
                    // Trust the declared size only if sane; streaming WAVs often write 0
                    // or 0xFFFFFFFF, so fall back to the rest of the file.
                    dataLen = if (size in 1..(b.size - body)) size else (b.size - body)
                    break // data is the last meaningful chunk
                }
            }
            if (size <= 0) break
            val next = body + size + (size and 1)
            if (next <= pos) break // guard against overflow / malformed sizes
            pos = next
        }

        if (dataOff < 0 || dataLen <= 0) return null
        return Wav(sampleRate, channels, bits, b.copyOfRange(dataOff, dataOff + dataLen))
    }
}
