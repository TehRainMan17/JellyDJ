package com.jellydj.mobile.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val JellyLight = lightColorScheme(
    primary = Color(0xFF2DC8D8),
    onPrimary = Color(0xFF090B22),
    secondary = Color(0xFF7D6BD6),
    onSecondary = Color(0xFFFFFFFF),
    background = Color(0xFFEEF3FF),
    onBackground = Color(0xFF121730),
    surface = Color(0xFFFFFFFF),
    onSurface = Color(0xFF121730)
)

private val JellyDark = darkColorScheme(
    primary = Color(0xFF53ECFC),
    onPrimary = Color(0xFF090B22),
    secondary = Color(0xFFA28FFB),
    onSecondary = Color(0xFF090B22),
    background = Color(0xFF090B22),
    onBackground = Color(0xFFE8EDF5),
    surface = Color(0xFF0F1333),
    onSurface = Color(0xFFE8EDF5)
)

@Composable
fun JellyDjTheme(content: @Composable () -> Unit) {
    val colors = if (isSystemInDarkTheme()) JellyDark else JellyLight
    MaterialTheme(
        colorScheme = colors,
        typography = Typography(),
        content = content
    )
}
