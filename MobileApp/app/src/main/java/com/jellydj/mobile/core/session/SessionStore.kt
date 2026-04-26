package com.jellydj.mobile.core.session

import android.content.Context

data class UserSession(
    val accessToken: String,
    val refreshToken: String,
    val userId: String,
    val username: String,
    val isAdmin: Boolean
)

interface SessionStore {
    fun save(session: UserSession)
    fun read(): UserSession?
    fun clear()
    fun updateAccessToken(accessToken: String)
    fun saveServerBaseUrl(url: String)
    fun readServerBaseUrl(): String?
    fun clearServerUrl()
}

class SharedPrefsSessionStore(context: Context) : SessionStore {
    private val prefs = context.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)

    override fun save(session: UserSession) {
        prefs.edit()
            .putString(KEY_ACCESS_TOKEN, session.accessToken)
            .putString(KEY_REFRESH_TOKEN, session.refreshToken)
            .putString(KEY_USER_ID, session.userId)
            .putString(KEY_USERNAME, session.username)
            .putBoolean(KEY_IS_ADMIN, session.isAdmin)
            .apply()
    }

    override fun read(): UserSession? {
        val accessToken = prefs.getString(KEY_ACCESS_TOKEN, null) ?: return null
        val refreshToken = prefs.getString(KEY_REFRESH_TOKEN, null) ?: return null
        val userId = prefs.getString(KEY_USER_ID, null) ?: return null
        val username = prefs.getString(KEY_USERNAME, null) ?: return null
        val isAdmin = prefs.getBoolean(KEY_IS_ADMIN, false)

        return UserSession(
            accessToken = accessToken,
            refreshToken = refreshToken,
            userId = userId,
            username = username,
            isAdmin = isAdmin
        )
    }

    override fun clear() {
        prefs.edit()
            .remove(KEY_ACCESS_TOKEN)
            .remove(KEY_REFRESH_TOKEN)
            .remove(KEY_USER_ID)
            .remove(KEY_USERNAME)
            .remove(KEY_IS_ADMIN)
            .apply()
    }

    override fun updateAccessToken(accessToken: String) {
        prefs.edit().putString(KEY_ACCESS_TOKEN, accessToken).apply()
    }

    override fun saveServerBaseUrl(url: String) {
        // Use commit() so an immediate verify request sees the new value reliably.
        prefs.edit().putString(KEY_SERVER_BASE_URL, url).commit()
    }

    override fun readServerBaseUrl(): String? {
        return prefs.getString(KEY_SERVER_BASE_URL, null)
    }

    override fun clearServerUrl() {
        prefs.edit().remove(KEY_SERVER_BASE_URL).apply()
    }

    companion object {
        private const val PREF_NAME = "jellydj_session"
        private const val KEY_ACCESS_TOKEN = "access_token"
        private const val KEY_REFRESH_TOKEN = "refresh_token"
        private const val KEY_USER_ID = "user_id"
        private const val KEY_USERNAME = "username"
        private const val KEY_IS_ADMIN = "is_admin"
        private const val KEY_SERVER_BASE_URL = "server_base_url"
    }
}
