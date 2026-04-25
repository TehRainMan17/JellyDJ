package com.jellydj.mobile.player

import android.content.ComponentName
import android.content.Context
import android.net.Uri
import android.util.Log
import androidx.media3.common.MediaItem
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import com.jellydj.mobile.core.model.Track
import com.google.common.util.concurrent.MoreExecutors
import com.google.common.util.concurrent.ListenableFuture
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.util.concurrent.ExecutionException

interface PlayerController {
    suspend fun playNow(track: Track)
    suspend fun playQueue(tracks: List<Track>, startIndex: Int)
}

class JellyDjPlayerController(context: Context) : PlayerController {
    private val appContext = context.applicationContext
    private val lock = Any()
    @Volatile
    private var controllerFuture: ListenableFuture<MediaController>? = null

    private fun buildControllerFuture(): ListenableFuture<MediaController> {
        return MediaController.Builder(
            appContext,
            SessionToken(appContext, ComponentName(appContext, JellyDjPlaybackService::class.java))
        ).buildAsync()
    }

    private fun getOrCreateControllerFuture(): ListenableFuture<MediaController> {
        synchronized(lock) {
            val existing = controllerFuture
            if (existing != null) return existing
            return buildControllerFuture().also { controllerFuture = it }
        }
    }

    private fun resetControllerFutureIfCurrent(current: ListenableFuture<MediaController>) {
        synchronized(lock) {
            if (controllerFuture === current) {
                MediaController.releaseFuture(current)
                controllerFuture = null
            }
        }
    }

    override suspend fun playNow(track: Track) {
        playQueue(listOf(track), 0)
    }

    override suspend fun playQueue(tracks: List<Track>, startIndex: Int) {
        if (tracks.isEmpty()) return

        withContext(Dispatchers.IO) {
            val mediaItems = tracks.filter { it.streamUrl.isNotBlank() }.map { track ->
                    MediaItem.Builder()
                        .setMediaId(track.id)
                        .setUri(track.streamUrl)
                        .setMediaMetadata(
                            androidx.media3.common.MediaMetadata.Builder()
                                .setTitle(track.title)
                                .setArtist(track.artist)
                                .setAlbumTitle(track.album)
                                .setIsPlayable(true)
                                .setArtworkUri(track.imageUrl?.let { Uri.parse(it) })
                                .build()
                        )
                        .build()
                }
            if (mediaItems.isEmpty()) {
                Log.e(TAG, "Playback skipped because all tracks had blank stream URLs.")
                return@withContext
            }

            val boundedIndex = startIndex.coerceIn(0, mediaItems.lastIndex)
            val firstAttempt = getOrCreateControllerFuture()
            val controller = try {
                firstAttempt.get()
            } catch (firstError: ExecutionException) {
                Log.w(TAG, "MediaController connection failed, retrying with a fresh session.", firstError)
                resetControllerFutureIfCurrent(firstAttempt)
                getOrCreateControllerFuture().get()
            }

            try {
                controller.setMediaItems(mediaItems, boundedIndex, 0L)
                controller.prepare()
                controller.play()
            } catch (playbackError: Throwable) {
                Log.e(TAG, "Playback request failed.", playbackError)
                throw playbackError
            }
        }
    }

    fun release() {
        val future = synchronized(lock) {
            controllerFuture.also { controllerFuture = null }
        } ?: return

        future.addListener({ MediaController.releaseFuture(future) }, MoreExecutors.directExecutor())
    }

    companion object {
        private const val TAG = "JellyDjPlayerController"
    }
}
