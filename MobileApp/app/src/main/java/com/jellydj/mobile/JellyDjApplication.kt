package com.jellydj.mobile

import android.app.Application
import android.util.Log
import coil.ImageLoader
import coil.ImageLoaderFactory
import com.jellydj.mobile.core.debug.AaTelemetry
import com.jellydj.mobile.core.session.SharedPrefsSessionStore

class JellyDjApplication : Application(), ImageLoaderFactory {
    lateinit var appContainer: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        appContainer = AppContainer(this, SharedPrefsSessionStore(this))
        AaTelemetry.init(this, appContainer.okHttpClient)
        installCrashHandler()
    }

    private fun installCrashHandler() {
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
            try {
                AaTelemetry.logCrashSync(
                    "uncaught_exception",
                    mapOf(
                        "thread" to thread.name,
                        "exception" to throwable.javaClass.name,
                        "message" to (throwable.message ?: ""),
                        "stack" to Log.getStackTraceString(throwable).take(8000),
                    )
                )
            } catch (_: Throwable) { }
            previous?.uncaughtException(thread, throwable)
        }
    }

    // Give Coil the same authenticated OkHttpClient used for API and ExoPlayer
    // requests so that proxied image URLs receive the JellyDJ JWT header.
    override fun newImageLoader(): ImageLoader {
        return ImageLoader.Builder(this)
            .okHttpClient(appContainer.okHttpClient)
            .build()
    }
}
