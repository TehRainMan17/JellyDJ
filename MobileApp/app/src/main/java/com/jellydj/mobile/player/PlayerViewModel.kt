package com.jellydj.mobile.player

import android.app.Application
import android.content.ComponentName
import android.net.Uri
import androidx.core.content.ContextCompat
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import com.google.common.util.concurrent.ListenableFuture
import com.jellydj.mobile.core.model.Track
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

data class PlayerUiState(
    val currentTitle: String? = null,
    val currentArtist: String? = null,
    val currentAlbumTitle: String? = null,
    val currentArtworkUri: String? = null,
    val isPlaying: Boolean = false,
    val positionMs: Long = 0L,
    val durationMs: Long = 0L,
    val hasMedia: Boolean = false,
    val shuffleEnabled: Boolean = false,
    val repeatMode: Int = Player.REPEAT_MODE_OFF,
    val queueSize: Int = 0,
    val currentQueueIndex: Int = 0
)

class PlayerViewModel(app: Application) : AndroidViewModel(app) {

    private val _uiState = MutableStateFlow(PlayerUiState())
    val uiState: StateFlow<PlayerUiState> = _uiState.asStateFlow()

    private var controller: MediaController? = null
    private lateinit var controllerFuture: ListenableFuture<MediaController>
    private var positionJob: Job? = null

    private val playerListener = object : Player.Listener {
        override fun onIsPlayingChanged(isPlaying: Boolean) {
            controller?.let { syncState(it) }
            if (isPlaying) startPositionPolling() else stopPositionPolling()
        }
        override fun onMediaItemTransition(item: MediaItem?, reason: Int) {
            controller?.let { syncState(it) }
        }
        override fun onPlaybackStateChanged(playbackState: Int) {
            controller?.let { syncState(it) }
        }
        override fun onShuffleModeEnabledChanged(shuffleModeEnabled: Boolean) {
            controller?.let { syncState(it) }
        }
        override fun onRepeatModeChanged(repeatMode: Int) {
            controller?.let { syncState(it) }
        }
    }

    init {
        connectToService()
    }

    private fun connectToService() {
        val ctx = getApplication<Application>().applicationContext
        val token = SessionToken(ctx, ComponentName(ctx, JellyDjPlaybackService::class.java))
        controllerFuture = MediaController.Builder(ctx, token).buildAsync()
        controllerFuture.addListener({
            try {
                val mc = controllerFuture.get()
                controller = mc
                mc.addListener(playerListener)
                syncState(mc)
                if (mc.isPlaying) startPositionPolling()
            } catch (_: Exception) { }
        }, ContextCompat.getMainExecutor(ctx))
    }

    private fun syncState(mc: MediaController) {
        val meta = mc.mediaMetadata
        _uiState.value = PlayerUiState(
            currentTitle = meta.title?.toString(),
            currentArtist = meta.artist?.toString(),
            currentAlbumTitle = meta.albumTitle?.toString(),
            currentArtworkUri = meta.artworkUri?.toString(),
            isPlaying = mc.isPlaying,
            positionMs = mc.currentPosition.coerceAtLeast(0L),
            durationMs = if (mc.duration == C.TIME_UNSET) 0L else mc.duration.coerceAtLeast(0L),
            hasMedia = mc.mediaItemCount > 0,
            shuffleEnabled = mc.shuffleModeEnabled,
            repeatMode = mc.repeatMode,
            queueSize = mc.mediaItemCount,
            currentQueueIndex = mc.currentMediaItemIndex
        )
    }

    private fun startPositionPolling() {
        positionJob?.cancel()
        positionJob = viewModelScope.launch {
            while (isActive) {
                delay(500)
                val mc = controller ?: break
                if (!mc.isPlaying) break
                _uiState.update { it.copy(positionMs = mc.currentPosition.coerceAtLeast(0L)) }
            }
        }
    }

    private fun stopPositionPolling() {
        positionJob?.cancel()
    }

    fun togglePlayPause() {
        val mc = controller ?: return
        if (mc.isPlaying) mc.pause() else mc.play()
    }

    fun next() {
        val mc = controller ?: return
        val nextIndex = mc.nextMediaItemIndex
        if (nextIndex != C.INDEX_UNSET) {
            mc.getMediaItemAt(nextIndex).mediaMetadata.let { meta ->
                _uiState.update { s ->
                    s.copy(
                        currentTitle = meta.title?.toString() ?: s.currentTitle,
                        currentArtist = meta.artist?.toString() ?: s.currentArtist,
                        currentAlbumTitle = meta.albumTitle?.toString() ?: s.currentAlbumTitle,
                        currentArtworkUri = meta.artworkUri?.toString(),
                        currentQueueIndex = nextIndex,
                        positionMs = 0L
                    )
                }
            }
        }
        mc.seekToNextMediaItem()
    }

    fun previous() {
        val mc = controller ?: return
        val prevIndex = mc.previousMediaItemIndex
        if (prevIndex != C.INDEX_UNSET && mc.currentPosition <= 3000L) {
            mc.getMediaItemAt(prevIndex).mediaMetadata.let { meta ->
                _uiState.update { s ->
                    s.copy(
                        currentTitle = meta.title?.toString() ?: s.currentTitle,
                        currentArtist = meta.artist?.toString() ?: s.currentArtist,
                        currentAlbumTitle = meta.albumTitle?.toString() ?: s.currentAlbumTitle,
                        currentArtworkUri = meta.artworkUri?.toString(),
                        currentQueueIndex = prevIndex,
                        positionMs = 0L
                    )
                }
            }
        } else {
            _uiState.update { it.copy(positionMs = 0L) }
        }
        mc.seekToPreviousMediaItem()
    }

    fun seekTo(ms: Long) = controller?.seekTo(ms)

    fun toggleShuffle() {
        val mc = controller ?: return
        mc.shuffleModeEnabled = !mc.shuffleModeEnabled
    }

    fun toggleRepeat() {
        val mc = controller ?: return
        mc.repeatMode = when (mc.repeatMode) {
            Player.REPEAT_MODE_OFF -> Player.REPEAT_MODE_ALL
            Player.REPEAT_MODE_ALL -> Player.REPEAT_MODE_ONE
            else -> Player.REPEAT_MODE_OFF
        }
    }

    fun playQueue(tracks: List<Track>, startIndex: Int) {
        val mc = controller ?: return
        val items = tracks.filter { it.streamUrl.isNotBlank() }.map { it.toMediaItem() }
        if (items.isEmpty()) return
        val idx = startIndex.coerceIn(0, items.lastIndex)
        mc.stop()
        mc.setMediaItems(items, idx, 0L)
        mc.prepare()
        mc.play()
    }

    fun playNow(track: Track) = playQueue(listOf(track), 0)

    override fun onCleared() {
        stopPositionPolling()
        controller?.removeListener(playerListener)
        if (::controllerFuture.isInitialized) {
            MediaController.releaseFuture(controllerFuture)
        }
    }
}

internal fun Track.toMediaItem(): MediaItem = MediaItem.Builder()
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
