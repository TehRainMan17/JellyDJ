package com.jellydj.mobile.player

import android.content.Intent
import android.net.Uri
import android.util.Log
import androidx.annotation.OptIn
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DataSourceBitmapLoader
import androidx.media3.datasource.cache.CacheDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.session.DefaultMediaNotificationProvider
import androidx.media3.session.LibraryResult
import androidx.media3.session.MediaLibraryService
import androidx.media3.session.MediaLibraryService.LibraryParams
import androidx.media3.session.MediaLibraryService.MediaLibrarySession
import androidx.media3.session.MediaSession
import com.google.common.collect.ImmutableList
import com.google.common.util.concurrent.ListenableFuture
import com.jellydj.mobile.JellyDjApplication
import com.jellydj.mobile.R
import com.jellydj.mobile.core.model.Track
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.guava.future
import kotlinx.coroutines.launch

@OptIn(UnstableApi::class)
class JellyDjPlaybackService : MediaLibraryService() {

    private lateinit var player: ExoPlayer
    private var mediaLibrarySession: MediaLibrarySession? = null
    private lateinit var resumeStore: PlaybackResumeStore
    private var audioEffects: AudioEffectsController? = null
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    override fun onCreate() {
        super.onCreate()
        val container = (application as JellyDjApplication).appContainer
        resumeStore = PlaybackResumeStore(this)

        // Use OkHttp-backed data source so the JellyDJ JWT is added as a header.
        // Stream URLs no longer embed Jellyfin tokens in query params.
        val upstreamFactory = container.audioDataSourceFactory
        val cache = container.simpleCache
        player = if (cache != null) {
            val cacheDataSourceFactory = CacheDataSource.Factory()
                .setCache(cache)
                .setUpstreamDataSourceFactory(upstreamFactory)
                .setFlags(CacheDataSource.FLAG_IGNORE_CACHE_ON_ERROR)
            ExoPlayer.Builder(this)
                .setMediaSourceFactory(DefaultMediaSourceFactory(cacheDataSourceFactory))
                .build()
        } else {
            ExoPlayer.Builder(this)
                .setMediaSourceFactory(DefaultMediaSourceFactory(upstreamFactory))
                .build()
        }

        val sessionId = player.audioSessionId

        audioEffects = try {
            AudioEffectsController(sessionId).also { ctrl ->
                container.eqBandInfoFlow.value = ctrl.bands
                ctrl.applySettings(container.audioSettingsFlow.value)
            }
        } catch (_: Exception) { null }

        serviceScope.launch {
            container.audioSettingsFlow.collect { settings ->
                audioEffects?.applySettings(settings)
            }
        }

        // Proactively refresh the session so Android Auto has a valid token immediately.
        serviceScope.launch(Dispatchers.IO) {
            if (container.sessionStore.read() != null) {
                try {
                    container.authRepository.refreshSession()
                } catch (e: Exception) {
                    Log.w(TAG, "Proactive session refresh failed: ${e.message}")
                }
            }
        }

        restoreQueueIfAvailable()
        observeQueueForResume()

        val callback = object : MediaLibrarySession.Callback {

            override fun onConnect(
                session: MediaSession,
                controller: MediaSession.ControllerInfo
            ): MediaSession.ConnectionResult {
                return if (isTrustedController(controller)) {
                    super.onConnect(session, controller)
                } else {
                    Log.w(TAG, "Rejected MediaBrowser connection from ${controller.packageName} (uid=${controller.uid})")
                    MediaSession.ConnectionResult.reject()
                }
            }

            override fun onGetLibraryRoot(
                session: MediaLibrarySession,
                browser: MediaSession.ControllerInfo,
                params: LibraryParams?
            ): ListenableFuture<LibraryResult<MediaItem>> {
                val root = MediaItem.Builder()
                    .setMediaId(ROOT_ID)
                    .setMediaMetadata(
                        MediaMetadata.Builder()
                            .setTitle("JellyDJ")
                            .setIsBrowsable(true)
                            .setIsPlayable(false)
                            .build()
                    )
                    .build()
                return serviceScope.future { LibraryResult.ofItem(root, params) }
            }

            override fun onGetItem(
                session: MediaLibrarySession,
                browser: MediaSession.ControllerInfo,
                mediaId: String
            ): ListenableFuture<LibraryResult<MediaItem>> {
                val item = MediaItem.Builder().setMediaId(mediaId).build()
                return serviceScope.future { LibraryResult.ofItem(item, null) }
            }

            override fun onGetChildren(
                session: MediaLibrarySession,
                browser: MediaSession.ControllerInfo,
                parentId: String,
                page: Int,
                pageSize: Int,
                params: LibraryParams?
            ): ListenableFuture<LibraryResult<ImmutableList<MediaItem>>> {
                return serviceScope.future {
                    try {
                        val children = ImmutableList.copyOf(loadChildren(parentId))
                        LibraryResult.ofItemList(children, params)
                    } catch (e: Throwable) {
                        Log.e(TAG, "Failed to load children for '$parentId'", e)
                        LibraryResult.ofItemList(
                            ImmutableList.of(signInRequiredItem()),
                            params
                        )
                    }
                }
            }
        }

        mediaLibrarySession = MediaLibrarySession.Builder(this, player, callback)
            .setBitmapLoader(DataSourceBitmapLoader(this))
            .build()

        val notificationProvider = DefaultMediaNotificationProvider.Builder(this)
            .setChannelName(R.string.playback_channel_name)
            .build()
        notificationProvider.setSmallIcon(R.drawable.ic_notification)
        setMediaNotificationProvider(notificationProvider)
    }

    private fun isTrustedController(controller: MediaSession.ControllerInfo): Boolean {
        // Our own app binds as a controller for playback control
        if (controller.packageName == packageName) return true
        // Android system process (notification shade, lock screen media controls)
        if (controller.uid == android.os.Process.SYSTEM_UID) return true
        // Known trusted media clients
        return controller.packageName in TRUSTED_PACKAGES
    }

    private suspend fun loadChildren(parentId: String): List<MediaItem> {
        val container = (application as JellyDjApplication).appContainer
        return when {
            parentId == ROOT_ID -> listOf(
                folderItem(RECENTS_ID, "Recently Played"),
                folderItem(PLAYLISTS_ID, "Playlists")
            )

            parentId == RECENTS_ID -> container.libraryRepository.recentlyPlayed()
                .take(100)
                .map { it.toPlayableMediaItem() }

            parentId == PLAYLISTS_ID -> container.libraryRepository.playlists().map {
                folderItem("$PLAYLIST_PREFIX${it.id}", it.name)
            }

            parentId.startsWith(PLAYLIST_PREFIX) -> {
                val playlistId = parentId.removePrefix(PLAYLIST_PREFIX)
                container.libraryRepository.playlistTracks(playlistId).map { it.toPlayableMediaItem() }
            }

            else -> emptyList()
        }
    }

    private fun restoreQueueIfAvailable() {
        val state = resumeStore.load() ?: return
        // Restore position as 0 — seeking to the exact saved position can land at end-of-stream
        // if the event fired during a track transition, leaving the player in STATE_ENDED.
        player.setMediaItems(state.items, state.index, 0L)
        player.prepare()
    }

    private fun observeQueueForResume() {
        player.addListener(object : Player.Listener {
            override fun onEvents(player: Player, events: Player.Events) {
                if (player.mediaItemCount == 0) return
                val items = buildList {
                    repeat(player.mediaItemCount) { index ->
                        add(player.getMediaItemAt(index))
                    }
                }
                resumeStore.save(items, player.currentMediaItemIndex, player.currentPosition)
            }
        })
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaLibrarySession? {
        return mediaLibrarySession
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        if (!player.playWhenReady || player.mediaItemCount == 0) {
            stopSelf()
        }
    }

    override fun onDestroy() {
        serviceScope.cancel()
        audioEffects?.release()
        mediaLibrarySession?.run {
            player.release()
            release()
            mediaLibrarySession = null
        }
        super.onDestroy()
    }

    private fun Track.toPlayableMediaItem(): MediaItem {
        return MediaItem.Builder()
            .setMediaId(id)
            .setUri(streamUrl)
            .setMediaMetadata(
                MediaMetadata.Builder()
                    .setTitle(title)
                    .setArtist(artist)
                    .setAlbumTitle(album)
                    .setIsPlayable(true)
                    .setArtworkUri(imageUrl?.let { Uri.parse(it) })
                    .build()
            )
            .build()
    }

    private fun folderItem(id: String, title: String): MediaItem {
        return MediaItem.Builder()
            .setMediaId(id)
            .setMediaMetadata(
                MediaMetadata.Builder()
                    .setTitle(title)
                    .setIsBrowsable(true)
                    .setIsPlayable(false)
                    .build()
            )
            .build()
    }

    private fun signInRequiredItem(): MediaItem {
        return MediaItem.Builder()
            .setMediaId("sign_in_required")
            .setMediaMetadata(
                MediaMetadata.Builder()
                    .setTitle("Open JellyDJ app to sign in")
                    .setIsBrowsable(false)
                    .setIsPlayable(false)
                    .build()
            )
            .build()
    }

    companion object {
        private const val TAG = "JellyDjService"
        private const val ROOT_ID = "jellydj_root"
        private const val RECENTS_ID = "recent"
        private const val PLAYLISTS_ID = "playlists"
        private const val PLAYLIST_PREFIX = "playlist:"

        private val TRUSTED_PACKAGES = setOf(
            "com.google.android.projection.gearhead", // Android Auto
            "com.google.android.carassistant",
            "com.android.systemui",
            "android",
        )
    }
}
