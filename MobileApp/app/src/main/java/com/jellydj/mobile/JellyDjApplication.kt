package com.jellydj.mobile

import android.app.Application
import coil.ImageLoader
import coil.ImageLoaderFactory
import com.jellydj.mobile.core.session.SharedPrefsSessionStore

class JellyDjApplication : Application(), ImageLoaderFactory {
    lateinit var appContainer: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        appContainer = AppContainer(this, SharedPrefsSessionStore(this))
    }

    // Give Coil the same authenticated OkHttpClient used for API and ExoPlayer
    // requests so that proxied image URLs receive the JellyDJ JWT header.
    override fun newImageLoader(): ImageLoader {
        return ImageLoader.Builder(this)
            .okHttpClient(appContainer.okHttpClient)
            .build()
    }
}
