package com.jellydj.mobile

import android.app.Application
import com.jellydj.mobile.core.session.SharedPrefsSessionStore

class JellyDjApplication : Application() {
    lateinit var appContainer: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        appContainer = AppContainer(this, SharedPrefsSessionStore(this))
    }
}
