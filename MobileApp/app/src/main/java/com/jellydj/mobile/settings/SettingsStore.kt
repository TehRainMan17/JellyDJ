package com.jellydj.mobile.settings

import android.content.Context
import android.content.SharedPreferences

class SettingsStore(context: Context) {
    private val prefs: SharedPreferences =
        context.getSharedPreferences("jellydj_audio_settings", Context.MODE_PRIVATE)

    fun save(settings: AudioSettings) {
        prefs.edit()
            .putBoolean(KEY_EQ_ENABLED, settings.eqEnabled)
            .putString(KEY_EQ_GAINS, settings.eqBandGains.joinToString(","))
            .putInt(KEY_VOLUME_BOOST, settings.volumeBoostMb)
            .putBoolean(KEY_CACHE_ENABLED, settings.cacheEnabled)
            .putInt(KEY_CACHE_SIZE, settings.cacheSizeMb)
            .apply()
    }

    fun load(): AudioSettings {
        val gainsStr = prefs.getString(KEY_EQ_GAINS, "") ?: ""
        val gains = if (gainsStr.isBlank()) emptyList()
                    else gainsStr.split(",").mapNotNull { it.toIntOrNull() }
        return AudioSettings(
            eqEnabled = prefs.getBoolean(KEY_EQ_ENABLED, false),
            eqBandGains = gains,
            volumeBoostMb = prefs.getInt(KEY_VOLUME_BOOST, 0),
            cacheEnabled = prefs.getBoolean(KEY_CACHE_ENABLED, false),
            cacheSizeMb = prefs.getInt(KEY_CACHE_SIZE, 512)
        )
    }

    companion object {
        private const val KEY_EQ_ENABLED = "eq_enabled"
        private const val KEY_EQ_GAINS = "eq_band_gains"
        private const val KEY_VOLUME_BOOST = "volume_boost_mb"
        private const val KEY_CACHE_ENABLED = "cache_enabled"
        private const val KEY_CACHE_SIZE = "cache_size_mb"
    }
}
