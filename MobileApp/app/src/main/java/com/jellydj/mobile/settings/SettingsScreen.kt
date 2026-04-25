package com.jellydj.mobile.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import kotlin.math.roundToInt

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    onBack: () -> Unit,
    settingsViewModel: SettingsViewModel = viewModel()
) {
    val settings by settingsViewModel.audioSettings.collectAsState()
    val bands by settingsViewModel.eqBands.collectAsState()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Audio Settings") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                }
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .padding(horizontal = 16.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            Spacer(Modifier.height(4.dp))

            EqSection(
                settings = settings,
                bands = bands,
                onEqEnabledChange = { settingsViewModel.setEqEnabled(it) },
                onBandGainChange = { index, gain -> settingsViewModel.setEqBandGain(index, gain) }
            )

            HorizontalDivider()

            VolumeBoostSection(
                boostMb = settings.volumeBoostMb,
                onBoostChange = { settingsViewModel.setVolumeBoostMb(it) }
            )

            HorizontalDivider()

            CacheSection(
                settings = settings,
                onCacheEnabledChange = { settingsViewModel.setCacheEnabled(it) },
                onCacheSizeChange = { settingsViewModel.setCacheSizeMb(it) },
                onClearCache = { settingsViewModel.clearCache() }
            )

            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
private fun EqSection(
    settings: AudioSettings,
    bands: List<EqBand>,
    onEqEnabledChange: (Boolean) -> Unit,
    onBandGainChange: (Int, Int) -> Unit
) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column {
                Text("Equalizer", style = MaterialTheme.typography.titleMedium)
                Text(
                    if (bands.isEmpty()) "Play a track first to enable EQ"
                    else "${bands.size} bands",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
            Switch(
                checked = settings.eqEnabled && bands.isNotEmpty(),
                onCheckedChange = onEqEnabledChange,
                enabled = bands.isNotEmpty()
            )
        }

        if (settings.eqEnabled && bands.isNotEmpty()) {
            bands.forEach { band ->
                val currentGain = settings.eqBandGains.getOrElse(band.index) { 0 }
                val range = (band.maxLevelMb - band.minLevelMb).toFloat()
                Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(formatFreq(band.centerFreqHz), style = MaterialTheme.typography.bodySmall)
                        Text(
                            formatGainDb(currentGain),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.primary
                        )
                    }
                    Slider(
                        value = (currentGain - band.minLevelMb).toFloat() / range,
                        onValueChange = { fraction ->
                            val gain = (band.minLevelMb + (fraction * range)).roundToInt()
                            onBandGainChange(band.index, gain)
                        }
                    )
                }
            }
        }
    }
}

@Composable
private fun VolumeBoostSection(
    boostMb: Int,
    onBoostChange: (Int) -> Unit
) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text("Volume Boost", style = MaterialTheme.typography.titleMedium)
            Text(
                if (boostMb == 0) "Off" else "+${boostMb / 100.0}dB",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.primary
            )
        }
        Slider(
            value = boostMb / 1000f,
            onValueChange = { onBoostChange((it * 1000).roundToInt()) }
        )
        Text(
            "Amplifies quiet tracks. Keep below +5dB to avoid distortion.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
    }
}

@Composable
private fun CacheSection(
    settings: AudioSettings,
    onCacheEnabledChange: (Boolean) -> Unit,
    onCacheSizeChange: (Int) -> Unit,
    onClearCache: () -> Unit
) {
    val cacheSizeOptions = listOf(256 to "256MB", 512 to "512MB", 1024 to "1GB", 2048 to "2GB")
    var showClearConfirm by remember { mutableStateOf(false) }

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column {
                Text("Offline Cache", style = MaterialTheme.typography.titleMedium)
                Text(
                    "Cache songs locally for offline playback",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
            Switch(checked = settings.cacheEnabled, onCheckedChange = onCacheEnabledChange)
        }

        if (settings.cacheEnabled) {
            Text("Cache Size", style = MaterialTheme.typography.bodyMedium)
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                cacheSizeOptions.forEach { (sizeMb, label) ->
                    FilterChip(
                        selected = settings.cacheSizeMb == sizeMb,
                        onClick = { onCacheSizeChange(sizeMb) },
                        label = { Text(label) }
                    )
                }
            }
            Text(
                "Cache size changes take effect after restarting the app.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
            OutlinedButton(
                onClick = { showClearConfirm = true },
                modifier = Modifier.fillMaxWidth()
            ) {
                Text("Clear Cache")
            }
        }
    }

    if (showClearConfirm) {
        AlertDialog(
            onDismissRequest = { showClearConfirm = false },
            title = { Text("Clear Cache?") },
            text = { Text("All locally cached audio will be deleted. Songs will re-download when played.") },
            confirmButton = {
                TextButton(onClick = { onClearCache(); showClearConfirm = false }) {
                    Text("Clear")
                }
            },
            dismissButton = {
                TextButton(onClick = { showClearConfirm = false }) { Text("Cancel") }
            }
        )
    }
}

private fun formatFreq(hz: Int): String = if (hz >= 1000) "${hz / 1000}kHz" else "${hz}Hz"

private fun formatGainDb(mb: Int): String {
    val db = mb / 100.0
    return when {
        db > 0 -> "+%.1fdB".format(db)
        db < 0 -> "%.1fdB".format(db)
        else -> "0dB"
    }
}
