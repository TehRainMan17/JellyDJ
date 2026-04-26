package com.jellydj.mobile

import android.content.Context
import androidx.annotation.OptIn
import androidx.media3.common.util.UnstableApi
import androidx.media3.database.StandaloneDatabaseProvider
import androidx.media3.datasource.cache.LeastRecentlyUsedCacheEvictor
import androidx.media3.datasource.cache.SimpleCache
import androidx.media3.datasource.okhttp.OkHttpDataSource
import com.jellydj.mobile.auth.AuthRepository
import com.jellydj.mobile.auth.JellyDjAuthRepository
import com.jellydj.mobile.core.network.JellyDjApi
import com.jellydj.mobile.core.network.JellyDjApiClientFactory
import com.jellydj.mobile.core.session.SessionStore
import com.jellydj.mobile.library.AlbumCatalogStore
import com.jellydj.mobile.library.JellyDjLibraryRepository
import com.jellydj.mobile.library.LibraryRepository
import com.jellydj.mobile.player.JellyDjPlayerController
import com.jellydj.mobile.player.PlayerController
import com.jellydj.mobile.search.JellyDjSearchRepository
import com.jellydj.mobile.search.SearchRepository
import com.jellydj.mobile.settings.AudioSettings
import com.jellydj.mobile.settings.EqBand
import com.jellydj.mobile.settings.SettingsStore
import com.jellydj.mobile.social.FakeSocialRepository
import com.jellydj.mobile.social.SocialRepository
import com.jellydj.mobile.vibe.FakeVibeRepository
import com.jellydj.mobile.vibe.VibeRepository
import kotlinx.coroutines.flow.MutableStateFlow
import okhttp3.OkHttpClient
import java.io.File

@OptIn(UnstableApi::class)
class AppContainer(
    context: Context,
    val sessionStore: SessionStore
) {
    // Shared OkHttpClient used for both Retrofit API calls and ExoPlayer stream requests.
    // Both paths need the auth interceptor so the JWT is added to every request.
    val okHttpClient: OkHttpClient = JellyDjApiClientFactory.createClient(sessionStore)

    private val api: JellyDjApi = JellyDjApiClientFactory.create(okHttpClient)

    // OkHttp-backed data source for ExoPlayer. Stream URLs no longer contain Jellyfin
    // tokens; the JWT is added by the auth interceptor in okHttpClient instead.
    val audioDataSourceFactory: OkHttpDataSource.Factory = OkHttpDataSource.Factory(okHttpClient)

    val settingsStore = SettingsStore(context)
    val albumCatalogStore = AlbumCatalogStore(context)
    val audioSettingsFlow = MutableStateFlow(settingsStore.load())
    val eqBandInfoFlow = MutableStateFlow<List<EqBand>>(emptyList())

    val simpleCache: SimpleCache? = run {
        val settings = settingsStore.load()
        if (!settings.cacheEnabled) return@run null
        try {
            val cacheDir = File(context.cacheDir, "jellydj_audio")
            val maxBytes = settings.cacheSizeMb.toLong() * 1024L * 1024L
            SimpleCache(
                cacheDir,
                LeastRecentlyUsedCacheEvictor(maxBytes),
                StandaloneDatabaseProvider(context)
            )
        } catch (e: Exception) {
            null
        }
    }

    val authRepository: AuthRepository = JellyDjAuthRepository(api, sessionStore)
    val libraryRepository: LibraryRepository = JellyDjLibraryRepository(api, { authRepository.refreshSession() }, albumCatalogStore)
    val searchRepository: SearchRepository = JellyDjSearchRepository(api) { authRepository.refreshSession() }
    val socialRepository: SocialRepository = FakeSocialRepository()
    val vibeRepository: VibeRepository = FakeVibeRepository()
    val playerController: PlayerController = JellyDjPlayerController(context)
}
