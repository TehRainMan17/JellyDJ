package com.jellydj.mobile.settings

import android.app.Application
import androidx.annotation.OptIn
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.media3.common.util.UnstableApi
import com.jellydj.mobile.JellyDjApplication
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

@OptIn(UnstableApi::class)
class SettingsViewModel(app: Application) : AndroidViewModel(app) {
    private val container = (app as JellyDjApplication).appContainer
    private val settingsStore = container.settingsStore

    val audioSettings: StateFlow<AudioSettings> = container.audioSettingsFlow.asStateFlow()

    val eqBands: StateFlow<List<EqBand>> = container.eqBandInfoFlow
        .stateIn(viewModelScope, SharingStarted.Eagerly, emptyList())

    fun setEqEnabled(enabled: Boolean) = updateSettings { it.copy(eqEnabled = enabled) }

    fun setEqBandGain(bandIndex: Int, gainMb: Int) = updateSettings { settings ->
        val gains = settings.eqBandGains.toMutableList()
        while (gains.size <= bandIndex) gains.add(0)
        gains[bandIndex] = gainMb
        settings.copy(eqBandGains = gains)
    }

    fun setVolumeBoostMb(boostMb: Int) = updateSettings { it.copy(volumeBoostMb = boostMb.coerceIn(0, 1000)) }

    fun setCacheEnabled(enabled: Boolean) = updateSettings { it.copy(cacheEnabled = enabled) }

    fun setCacheSizeMb(sizeMb: Int) = updateSettings { it.copy(cacheSizeMb = sizeMb) }

    fun clearCache() {
        val cache = container.simpleCache ?: return
        viewModelScope.launch {
            try {
                cache.keys.toList().forEach { key -> cache.removeResource(key) }
            } catch (_: Exception) {}
        }
    }

    private fun updateSettings(transform: (AudioSettings) -> AudioSettings) {
        val newSettings = transform(container.audioSettingsFlow.value)
        container.audioSettingsFlow.value = newSettings
        settingsStore.save(newSettings)
    }
}
