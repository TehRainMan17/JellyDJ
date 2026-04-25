package com.jellydj.mobile

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import com.jellydj.mobile.navigation.JellyDjMobileApp
import com.jellydj.mobile.player.PlayerViewModel
import com.jellydj.mobile.ui.theme.JellyDjTheme

class MainActivity : ComponentActivity() {

    private val playerViewModel: PlayerViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val container = (application as JellyDjApplication).appContainer

        setContent {
            JellyDjTheme {
                JellyDjMobileApp(container, playerViewModel)
            }
        }
    }
}
