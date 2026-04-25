package com.jellydj.mobile.settings

data class AudioSettings(
    val eqEnabled: Boolean = false,
    val eqBandGains: List<Int> = emptyList(),  // millibels per band
    val volumeBoostMb: Int = 0,                 // 0..1000 millibels (+0 to +10 dB)
    val cacheEnabled: Boolean = false,
    val cacheSizeMb: Int = 512
)

data class EqBand(
    val index: Int,
    val centerFreqHz: Int,
    val minLevelMb: Int,
    val maxLevelMb: Int
)
