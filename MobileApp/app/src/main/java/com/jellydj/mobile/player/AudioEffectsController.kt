package com.jellydj.mobile.player

import android.media.audiofx.Equalizer
import android.media.audiofx.LoudnessEnhancer
import android.util.Log
import com.jellydj.mobile.settings.AudioSettings
import com.jellydj.mobile.settings.EqBand

class AudioEffectsController(audioSessionId: Int) {
    private var equalizer: Equalizer? = null
    private var loudnessEnhancer: LoudnessEnhancer? = null

    val bands: List<EqBand>

    init {
        var eq: Equalizer? = null
        var le: LoudnessEnhancer? = null
        try {
            eq = Equalizer(0, audioSessionId)
            le = LoudnessEnhancer(audioSessionId)
        } catch (e: Exception) {
            Log.w(TAG, "Audio effects unavailable on this device", e)
        }
        equalizer = eq
        loudnessEnhancer = le
        bands = buildBandList(eq)
    }

    private fun buildBandList(eq: Equalizer?): List<EqBand> {
        eq ?: return emptyList()
        val range = try { eq.bandLevelRange } catch (_: Exception) { return emptyList() }
        return (0 until eq.numberOfBands.toInt()).map { i ->
            EqBand(
                index = i,
                centerFreqHz = try { eq.getCenterFreq(i.toShort()) / 1000 } catch (_: Exception) { 0 },
                minLevelMb = range[0].toInt(),
                maxLevelMb = range[1].toInt()
            )
        }
    }

    fun applySettings(settings: AudioSettings) {
        applyEq(settings)
        applyVolumeBoost(settings)
    }

    private fun applyEq(settings: AudioSettings) {
        val eq = equalizer ?: return
        try {
            if (settings.eqEnabled && settings.eqBandGains.size == bands.size) {
                settings.eqBandGains.forEachIndexed { i, gain ->
                    eq.setBandLevel(i.toShort(), gain.toShort())
                }
                eq.enabled = true
            } else {
                eq.enabled = false
            }
        } catch (e: Exception) {
            Log.w(TAG, "EQ apply failed", e)
        }
    }

    private fun applyVolumeBoost(settings: AudioSettings) {
        val le = loudnessEnhancer ?: return
        try {
            if (settings.volumeBoostMb > 0) {
                le.setTargetGain(settings.volumeBoostMb)
                le.enabled = true
            } else {
                le.enabled = false
            }
        } catch (e: Exception) {
            Log.w(TAG, "Volume boost apply failed", e)
        }
    }

    fun release() {
        try { equalizer?.release() } catch (_: Exception) {}
        try { loudnessEnhancer?.release() } catch (_: Exception) {}
        equalizer = null
        loudnessEnhancer = null
    }

    companion object {
        private const val TAG = "AudioEffectsController"
    }
}
