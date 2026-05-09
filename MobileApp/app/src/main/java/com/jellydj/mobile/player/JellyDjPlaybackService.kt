package com.jellydj.mobile.player

import android.app.PendingIntent
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.util.Log
import com.jellydj.mobile.MainActivity
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
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
import com.jellydj.mobile.JellyDjApplication
import com.jellydj.mobile.R
import com.jellydj.mobile.core.debug.AaTelemetry
import com.jellydj.mobile.core.model.Track
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.guava.future
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout

@OptIn(UnstableApi::class)
class JellyDjPlaybackService : MediaLibraryService() {

    private lateinit var player: ExoPlayer
    private var mediaLibrarySession: MediaLibrarySession? = null
    private lateinit var resumeStore: PlaybackResumeStore
    private var audioEffects: AudioEffectsController? = null
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    // Short-lived cache so rapid Android Auto browse events don't hammer Jellyfin.
    // All access is on Dispatchers.Main via serviceScope — no locking needed.
    private val browseCache = mutableMapOf<String, Pair<Long, List<MediaItem>>>()

    // Tracks every playable MediaItem we've handed out, keyed by mediaId. When AA sends a tap
    // back through the controller, Media3 strips the URI across the IPC boundary — only the
    // mediaId survives. Without this resolution map, the player gets a URI-less item and
    // perpetually shows "fetching your selection". onAddMediaItems re-attaches the full item.
    private val playableByMediaId = mutableMapOf<String, MediaItem>()

    // Auto queue restore on service start has been removed. Telemetry proved it was the root
    // cause of the Android Auto launch failure: any non-empty timeline at AA's bind time makes
    // AA enter Now Playing mode and stop browsing, regardless of player state. The resume
    // writer (observeQueueForResume) still persists state, so a future explicit "resume last
    // session" action triggered by the in-app UI can call into resumeStore at user intent.

    override fun onCreate() {
        val onCreateStartMs = System.currentTimeMillis()
        AaTelemetry.log("service_oncreate_start")
        super.onCreate()

        val container = (application as JellyDjApplication).appContainer
        resumeStore = PlaybackResumeStore(this)
        AaTelemetry.log("service_container_ready", mapOf(
            "has_session" to (container.sessionStore.read() != null),
            "has_cache" to (container.simpleCache != null),
            "base_url_set" to (container.sessionStore.readServerBaseUrl() != null),
        ))

        // Build player first — everything below depends on it.
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

        // AudioEffectsController creates Equalizer via IPC to AudioFlinger. When the phone is
        // connected to a car (Bluetooth A2DP), AudioFlinger is busy with audio routing and that
        // IPC can block for 10–30+ seconds, causing an ANR that freezes the whole phone.
        // Run it on IO; update the shared state flows back on Main when done.
        serviceScope.launch(Dispatchers.IO) {
            val effectsStartMs = System.currentTimeMillis()
            AaTelemetry.log("audio_effects_init_start", mapOf("audio_session_id" to sessionId))
            var errorClass: String? = null
            var errorMessage: String? = null
            val ctrl = try {
                AudioEffectsController(sessionId).also { it.applySettings(container.audioSettingsFlow.value) }
            } catch (e: Exception) {
                errorClass = e.javaClass.name
                errorMessage = e.message
                null
            }
            AaTelemetry.log("audio_effects_init_end", mapOf(
                "duration_ms" to (System.currentTimeMillis() - effectsStartMs),
                "ok" to (ctrl != null),
                "error_class" to errorClass,
                "error_message" to errorMessage,
            ))
            withContext(Dispatchers.Main) {
                audioEffects = ctrl
                container.eqBandInfoFlow.value = ctrl?.bands ?: emptyList()
            }
        }

        serviceScope.launch {
            container.audioSettingsFlow.collect { settings ->
                // Capture on Main, apply on IO — audio effect IPC must not run on Main.
                val ctrl = audioEffects
                serviceScope.launch(Dispatchers.IO) { ctrl?.applySettings(settings) }
            }
        }

        // Proactively refresh the session so Android Auto has a valid token immediately.
        serviceScope.launch(Dispatchers.IO) {
            if (container.sessionStore.read() != null) {
                val refreshStartMs = System.currentTimeMillis()
                AaTelemetry.log("session_refresh_start")
                var ok = true
                var errorClass: String? = null
                var errorMessage: String? = null
                try {
                    container.authRepository.refreshSession()
                } catch (e: Exception) {
                    ok = false
                    errorClass = e.javaClass.name
                    errorMessage = e.message
                    Log.w(TAG, "Proactive session refresh failed: ${e.message}")
                }
                AaTelemetry.log("session_refresh_end", mapOf(
                    "duration_ms" to (System.currentTimeMillis() - refreshStartMs),
                    "ok" to ok,
                    "error_class" to errorClass,
                    "error_message" to errorMessage,
                ))
            } else {
                AaTelemetry.log("session_refresh_skipped_no_session")
            }
        }

        // Attach the resume-state writer immediately so any playback (in-app or AA-initiated)
        // is captured. Queue *restoration* is deferred to onConnect — see queueRestored.
        try {
            observeQueueForResume()
        } catch (e: Throwable) {
            Log.e(TAG, "Non-fatal setup error (resume observer skipped): ${e.message}", e)
        }

        val callback = object : MediaLibrarySession.Callback {

            override fun onConnect(
                session: MediaSession,
                controller: MediaSession.ControllerInfo
            ): MediaSession.ConnectionResult {
                AaTelemetry.log("on_connect", mapOf(
                    "caller_package" to controller.packageName,
                    "caller_uid" to controller.uid,
                    "is_self" to (controller.packageName == packageName),
                    "is_known" to (controller.packageName in KNOWN_PACKAGES),
                    "media_item_count" to player.mediaItemCount,
                    "playback_state" to player.playbackState,
                    "is_playing" to player.isPlaying,
                ))
                // Accept all connections. A prior package allowlist was rejecting Android Auto
                // when it connected via the legacy MediaBrowserService compat path, where the
                // effective package identity seen by Media3 may not match the AA app package.
                // The browse cache (BROWSE_CACHE_TTL_MS) guards Jellyfin from rapid re-queries
                // from any client. Unknown callers are logged for diagnostics only.
                if (controller.packageName != packageName &&
                    controller.uid != android.os.Process.SYSTEM_UID &&
                    controller.packageName !in KNOWN_PACKAGES
                ) {
                    Log.i(TAG, "Unknown controller connected: ${controller.packageName} uid=${controller.uid}")
                }

                // Restore the saved queue ONLY when our own app's UI binds. Doing it for AA
                // (or any external browser) would force the player into STATE_BUFFERING on
                // a possibly-stale URL, which AA renders as a loading throbber on a black
                // screen forever.
                return super.onConnect(session, controller)
            }

            override fun onGetLibraryRoot(
                session: MediaLibrarySession,
                browser: MediaSession.ControllerInfo,
                params: LibraryParams?
            ): ListenableFuture<LibraryResult<MediaItem>> {
                AaTelemetry.log("on_get_library_root", mapOf(
                    "caller_package" to browser.packageName,
                    "caller_uid" to browser.uid,
                    "is_recent" to (params?.isRecent == true),
                    "is_offline" to (params?.isOffline == true),
                    "is_suggested" to (params?.isSuggested == true),
                    "media_item_count" to player.mediaItemCount,
                    "playback_state" to player.playbackState,
                    "is_playing" to player.isPlaying,
                ))
                // Always return the normal root, including for params.isRecent=true. Gear Head
                // probes with isRecent on launch; returning RESULT_ERROR_NOT_SUPPORTED there made
                // AA treat the library as unavailable and stall forever on the loading throbber.
                // Android Auto requires the root metadata to declare a media type — without it
                // AA accepts the root but never requests children because it cannot determine
                // the browse layout. MEDIA_TYPE_FOLDER_MIXED tells AA the root contains a mix
                // of categories (playlists + tracks).
                val extras = Bundle().apply {
                    putBoolean("android.media.browse.CONTENT_STYLE_SUPPORTED", true)
                    putInt("android.media.browse.CONTENT_STYLE_BROWSABLE_HINT", 1)
                    putInt("android.media.browse.CONTENT_STYLE_PLAYABLE_HINT", 1)
                    putBoolean("android.media.browse.SEARCH_SUPPORTED", false)
                }
                val root = MediaItem.Builder()
                    .setMediaId(ROOT_ID)
                    .setMediaMetadata(
                        MediaMetadata.Builder()
                            .setTitle("JellyDJ")
                            .setIsBrowsable(true)
                            .setIsPlayable(false)
                            .setMediaType(MediaMetadata.MEDIA_TYPE_FOLDER_MIXED)
                            // Stamp the same extras on the MediaMetadata so the legacy
                            // MediaBrowserCompat bridge surfaces them as BrowserRoot extras —
                            // some Gear Head versions only read these from the root item.
                            .setExtras(Bundle(extras))
                            .build()
                    )
                    .build()
                // Do NOT echo Gear Head's caller extras back (they include
                // KEY_ROOT_CHILDREN_LIMIT and similar constraints we don't honor — echoing
                // them back signals false acceptance and AA may stop the browse handshake).
                val rootParams = LibraryParams.Builder().setExtras(extras).build()
                AaTelemetry.log("on_get_library_root_returning", mapOf(
                    "root_id" to ROOT_ID,
                    "caller_extras_keys" to (params?.extras?.keySet()?.joinToString(",") ?: ""),
                ))
                // Return synchronously — Main dispatcher is sometimes busy during AA bind
                // and we want to eliminate that as a stall variable.
                return Futures.immediateFuture(LibraryResult.ofItem(root, rootParams))
            }

            override fun onGetItem(
                session: MediaLibrarySession,
                browser: MediaSession.ControllerInfo,
                mediaId: String
            ): ListenableFuture<LibraryResult<MediaItem>> {
                val cached = playableByMediaId[mediaId]
                val item = cached ?: MediaItem.Builder().setMediaId(mediaId).build()
                return Futures.immediateFuture(LibraryResult.ofItem(item, null))
            }

            // CRITICAL: Media3's IPC boundary strips MediaItem.localConfiguration (URI) when
            // a controller (Android Auto) hands a tapped item back to the session. Resolve
            // each item from our cache so the player gets a fully-formed item with a URI.
            override fun onAddMediaItems(
                mediaSession: MediaSession,
                controller: MediaSession.ControllerInfo,
                mediaItems: MutableList<MediaItem>
            ): ListenableFuture<MutableList<MediaItem>> {
                val resolved: MutableList<MediaItem> = mediaItems.map { item ->
                    playableByMediaId[item.mediaId] ?: item
                }.toMutableList()
                AaTelemetry.log("on_add_media_items", mapOf(
                    "caller_package" to controller.packageName,
                    "requested_count" to mediaItems.size,
                    "resolved_count" to resolved.count { it.localConfiguration?.uri != null },
                ))
                return Futures.immediateFuture(resolved)
            }

            override fun onGetChildren(
                session: MediaLibrarySession,
                browser: MediaSession.ControllerInfo,
                parentId: String,
                page: Int,
                pageSize: Int,
                params: LibraryParams?
            ): ListenableFuture<LibraryResult<ImmutableList<MediaItem>>> {
                val callerPackage = browser.packageName
                return serviceScope.future {
                    val startMs = System.currentTimeMillis()
                    AaTelemetry.log("on_get_children_start", mapOf(
                        "caller_package" to callerPackage,
                        "parent_id" to parentId,
                        "page" to page,
                        "page_size" to pageSize,
                    ))
                    try {
                        // Root is static — never block AA on it. For non-root, allow up to 4s
                        // (AA's hard limit is generous; 1.5s was tripping on slow Jellyfin and
                        // returning errors that AA renders as a permanent throbber).
                        val children = if (parentId == ROOT_ID) {
                            ImmutableList.copyOf(loadChildren(parentId))
                        } else {
                            withTimeout(4_000) {
                                ImmutableList.copyOf(loadChildren(parentId))
                            }
                        }
                        AaTelemetry.log("on_get_children_end", mapOf(
                            "caller_package" to callerPackage,
                            "parent_id" to parentId,
                            "count" to children.size,
                            "duration_ms" to (System.currentTimeMillis() - startMs),
                        ))
                        LibraryResult.ofItemList(children, params)
                    } catch (e: Throwable) {
                        Log.e(TAG, "Failed to load children for '$parentId'", e)
                        AaTelemetry.log("on_get_children_error", mapOf(
                            "caller_package" to callerPackage,
                            "parent_id" to parentId,
                            "duration_ms" to (System.currentTimeMillis() - startMs),
                            "error_class" to e.javaClass.name,
                            "error_message" to (e.message ?: ""),
                        ))
                        // Return a single visible item so AA shows *something* and the user
                        // can recover, rather than spinning forever on an error result.
                        LibraryResult.ofItemList(
                            ImmutableList.of(signInRequiredItem()),
                            params
                        )
                    }
                }
            }
        }

        // Android Auto (Gear Head) requires a launch PendingIntent on the session.
        // Without this, some AA versions accept the library root but never request
        // children — they refuse to render a browse tree they cannot launch the app from.
        val launchIntent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP
        }
        val sessionActivity = PendingIntent.getActivity(
            this,
            0,
            launchIntent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        // NOTE: artwork URLs (/api/mobile/image/{id}) currently require JWT auth that the
        // default bitmap loader cannot attach — covers will be blank in AA until the image
        // endpoint is exposed via a tokenless route OR we wire an OkHttp-backed BitmapLoader.
        // Replacing the bitmap loader with the OkHttp DataSource.Factory at session-build
        // time crashed MediaLibrarySession construction on Media3 1.4.1, so revert to default.
        mediaLibrarySession = MediaLibrarySession.Builder(this, player, callback)
            .setBitmapLoader(DataSourceBitmapLoader(this))
            .setSessionActivity(sessionActivity)
            .build()

        val notificationProvider = DefaultMediaNotificationProvider.Builder(this)
            .setChannelName(R.string.playback_channel_name)
            .build()
        notificationProvider.setSmallIcon(R.drawable.ic_notification)
        setMediaNotificationProvider(notificationProvider)

        Log.i(TAG, "Service started, session ready")
        AaTelemetry.log("service_oncreate_end", mapOf(
            "duration_ms" to (System.currentTimeMillis() - onCreateStartMs)
        ))
    }

    private suspend fun loadChildren(parentId: String): List<MediaItem> {
        // Root items are static — skip the cache entirely.
        if (parentId == ROOT_ID) {
            return listOf(
                folderItem(RECENTS_ID, "Recently Played", MediaMetadata.MEDIA_TYPE_PLAYLIST,
                    style = CONTENT_STYLE_GRID_ITEM),
                folderItem(PLAYLISTS_ID, "Playlists", MediaMetadata.MEDIA_TYPE_FOLDER_PLAYLISTS,
                    style = CONTENT_STYLE_GRID_ITEM)
            )
        }

        // Serve from cache if still fresh to avoid hammering Jellyfin.
        val now = System.currentTimeMillis()
        browseCache[parentId]?.let { (ts, cached) ->
            if (now - ts < BROWSE_CACHE_TTL_MS) {
                cached.forEach { if (it.localConfiguration?.uri != null) playableByMediaId[it.mediaId] = it }
                return cached
            }
        }

        val container = (application as JellyDjApplication).appContainer
        val items = when {
            parentId == RECENTS_ID -> container.libraryRepository.recentlyPlayed()
                .take(100)
                .map { it.toPlayableMediaItem() }

            parentId == PLAYLISTS_ID -> container.libraryRepository.playlists().map {
                folderItem(
                    id = "$PLAYLIST_PREFIX${it.id}",
                    title = it.name,
                    mediaType = MediaMetadata.MEDIA_TYPE_PLAYLIST,
                    artworkUri = it.coverImageUrl?.takeIf { url -> url.isNotBlank() }?.let { url -> Uri.parse(url) },
                    style = CONTENT_STYLE_GRID_ITEM,
                    subtitle = if (it.trackCount > 0) "${it.trackCount} tracks" else null,
                )
            }

            parentId.startsWith(PLAYLIST_PREFIX) -> {
                val playlistId = parentId.removePrefix(PLAYLIST_PREFIX)
                container.libraryRepository.playlistTracks(playlistId).map { it.toPlayableMediaItem() }
            }

            else -> emptyList()
        }

        browseCache[parentId] = Pair(now, items)
        items.forEach { if (it.localConfiguration?.uri != null) playableByMediaId[it.mediaId] = it }
        return items
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
        AaTelemetry.log("on_task_removed", mapOf(
            "play_when_ready" to player.playWhenReady,
            "media_item_count" to player.mediaItemCount,
        ))
        if (!player.playWhenReady || player.mediaItemCount == 0) {
            stopSelf()
        }
    }

    override fun onDestroy() {
        AaTelemetry.log("service_ondestroy")
        // Release audio effects on a detached thread — AudioFlinger IPC must not block onDestroy.
        val effectsToRelease = audioEffects
        audioEffects = null
        if (effectsToRelease != null) {
            Thread { effectsToRelease.release() }.start()
        }
        serviceScope.cancel()
        mediaLibrarySession?.run {
            player.release()
            release()
            mediaLibrarySession = null
        }
        super.onDestroy()
    }

    private fun Track.toPlayableMediaItem(): MediaItem {
        val extras = Bundle().apply {
            putInt("android.media.browse.CONTENT_STYLE_PLAYABLE_HINT", CONTENT_STYLE_LIST_ITEM)
        }
        return MediaItem.Builder()
            .setMediaId(id)
            .setUri(streamUrl)
            .setMediaMetadata(
                MediaMetadata.Builder()
                    .setTitle(title)
                    .setArtist(artist)
                    .setAlbumTitle(album)
                    .setSubtitle(artist)
                    .setIsBrowsable(false)
                    .setIsPlayable(true)
                    .setMediaType(MediaMetadata.MEDIA_TYPE_MUSIC)
                    .setArtworkUri(imageUrl?.takeIf { it.isNotBlank() }?.let { Uri.parse(it) })
                    .setExtras(extras)
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

    private fun folderItem(
        id: String,
        title: String,
        mediaType: Int = MediaMetadata.MEDIA_TYPE_FOLDER_MIXED,
        artworkUri: Uri? = null,
        style: Int = CONTENT_STYLE_LIST_ITEM,
        subtitle: String? = null,
    ): MediaItem {
        val extras = Bundle().apply {
            putInt("android.media.browse.CONTENT_STYLE_BROWSABLE_HINT", style)
        }
        return MediaItem.Builder()
            .setMediaId(id)
            .setMediaMetadata(
                MediaMetadata.Builder()
                    .setTitle(title)
                    .setSubtitle(subtitle)
                    .setIsBrowsable(true)
                    .setIsPlayable(false)
                    .setMediaType(mediaType)
                    .setArtworkUri(artworkUri)
                    .setExtras(extras)
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
        private const val BROWSE_CACHE_TTL_MS = 30_000L

        // Android Auto content style constants. Surfaced via metadata extras keys
        // "android.media.browse.CONTENT_STYLE_BROWSABLE_HINT" /
        // "android.media.browse.CONTENT_STYLE_PLAYABLE_HINT".
        private const val CONTENT_STYLE_LIST_ITEM = 1
        private const val CONTENT_STYLE_GRID_ITEM = 2

        private val KNOWN_PACKAGES = setOf(
            "com.google.android.projection.gearhead",
            "com.google.android.carassistant",
            "com.android.systemui",
            "android",
        )
    }
}
