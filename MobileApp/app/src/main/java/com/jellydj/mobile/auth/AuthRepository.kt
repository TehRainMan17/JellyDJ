package com.jellydj.mobile.auth

import com.jellydj.mobile.core.network.JellyDjApi
import com.jellydj.mobile.core.network.LoginRequest
import com.jellydj.mobile.core.network.RefreshRequest
import com.jellydj.mobile.core.session.SessionStore
import com.jellydj.mobile.core.session.UserSession
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

data class LoginInput(
    val username: String,
    val password: String,
    val rememberMe: Boolean = false
)

data class AuthUser(
    val id: String,
    val username: String,
    val isAdmin: Boolean
)

interface AuthRepository {
    suspend fun verifyInstance(baseUrl: String): Result<Unit>
    suspend fun login(input: LoginInput): Result<AuthUser>
    suspend fun currentUser(): Result<AuthUser>
    suspend fun refreshSession(): Boolean
    fun logout()
}

class JellyDjAuthRepository(
    private val api: JellyDjApi,
    private val sessionStore: SessionStore
) : AuthRepository {
    override suspend fun verifyInstance(baseUrl: String): Result<Unit> {
        val previous = sessionStore.readServerBaseUrl()
        val normalized = normalizeBaseUrl(baseUrl)
        sessionStore.saveServerBaseUrl(normalized)

        return runCatching {
            val health = api.health()
            if (health.status.lowercase() != "ok") {
                throw IllegalStateException("Server did not return healthy status.")
            }
        }.onFailure {
            if (previous != null) sessionStore.saveServerBaseUrl(previous)
        }
    }

    override suspend fun login(input: LoginInput): Result<AuthUser> {
        return try {
            val response = api.login(
                LoginRequest(
                    username = input.username,
                    password = input.password,
                    remember_me = input.rememberMe
                )
            )

            sessionStore.save(
                UserSession(
                    accessToken = response.access_token,
                    refreshToken = response.refresh_token,
                    userId = "",
                    username = response.username,
                    isAdmin = response.is_admin
                )
            )

            val me = api.me()
            sessionStore.save(
                UserSession(
                    accessToken = response.access_token,
                    refreshToken = response.refresh_token,
                    userId = me.user_id,
                    username = me.username,
                    isAdmin = me.is_admin
                )
            )

            Result.success(AuthUser(id = me.user_id, username = me.username, isAdmin = me.is_admin))
        } catch (t: Throwable) {
            sessionStore.clear()
            Result.failure(t)
        }
    }

    override suspend fun currentUser(): Result<AuthUser> = runCatching {
        val me = api.me()
        AuthUser(id = me.user_id, username = me.username, isAdmin = me.is_admin)
    }

    override suspend fun refreshSession(): Boolean {
        val current = sessionStore.read() ?: return false

        return runCatching {
            val refreshed = api.refresh(RefreshRequest(refresh_token = current.refreshToken))
            sessionStore.save(
                current.copy(
                    accessToken = refreshed.access_token,
                    refreshToken = refreshed.refresh_token
                )
            )
            true
        }.getOrDefault(false)
    }

    override fun logout() {
        sessionStore.clear()
    }

    private fun normalizeBaseUrl(url: String): String {
        val trimmed = url.trim()
        require(trimmed.isNotBlank()) { "Server URL is required." }

        val cleaned = trimmed.replace(" ", "")
        val (scheme, remainderRaw) = splitScheme(cleaned)
        val remainder = remainderRaw
            .removePrefix("//")
            .replace(Regex("^(?i)https?:/+"), "")
        val withScheme = "$scheme://$remainder"
        val parsed = withScheme.toHttpUrlOrNull()
            ?: throw IllegalArgumentException("Invalid server URL.")

        val path = sanitizePath(parsed.encodedPath)
        val port = when {
            parsed.scheme == "http" && parsed.port == 80 -> ""
            parsed.scheme == "https" && parsed.port == 443 -> ""
            else -> ":${parsed.port}"
        }

        return buildString {
            append(parsed.scheme)
            append("://")
            append(parsed.host)
            append(port)
            if (path.isNotBlank()) {
                append(path)
            }
        }
    }

    private fun sanitizePath(path: String): String {
        var normalized = path.trimEnd('/').takeIf { it != "/" }.orEmpty()
        normalized = removeSuffixIgnoreCase(normalized, "/api/health")
        normalized = removeSuffixIgnoreCase(normalized, "/health")
        normalized = removeSuffixIgnoreCase(normalized, "/api")
        return normalized
    }

    private fun removeSuffixIgnoreCase(value: String, suffix: String): String {
        return if (value.lowercase().endsWith(suffix.lowercase())) {
            value.dropLast(suffix.length)
        } else {
            value
        }
    }

    private fun splitScheme(input: String): Pair<String, String> {
        val full = Regex("^(?i)(https?)://(.+)$").matchEntire(input)
        if (full != null) {
            return full.groupValues[1].lowercase() to full.groupValues[2]
        }

        val malformed = Regex("^(?i)(https?):/+(.+)$").matchEntire(input)
        if (malformed != null) {
            return malformed.groupValues[1].lowercase() to malformed.groupValues[2]
        }

        return "http" to input
    }

}
