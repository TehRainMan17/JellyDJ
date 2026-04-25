package com.jellydj.mobile.library

import com.jellydj.mobile.core.model.Playlist
import com.jellydj.mobile.core.model.Track
import com.jellydj.mobile.core.network.JellyDjApi
import retrofit2.HttpException

interface LibraryRepository {
    suspend fun recentlyPlayed(): List<Track>
    suspend fun topTracks(): List<Track>
    suspend fun playlists(): List<Playlist>
    suspend fun playlistTracks(playlistId: String): List<Track>
}

class JellyDjLibraryRepository(
    private val api: JellyDjApi,
    private val refreshSession: suspend () -> Boolean
) : LibraryRepository {

    override suspend fun recentlyPlayed(): List<Track> = withRefresh {
        api.recent().map { it.toDomain() }
    }

    override suspend fun topTracks(): List<Track> = withRefresh {
        api.topTracks().map { it.toDomain() }
    }

    override suspend fun playlists(): List<Playlist> = withRefresh {
        api.playlists().map {
            Playlist(
                id = it.id,
                name = it.name,
                owner = "You",
                trackCount = it.track_count,
                isCollaborative = false,
                coverImageUrl = it.cover_image_url
            )
        }
    }

    override suspend fun playlistTracks(playlistId: String): List<Track> = withRefresh {
        api.playlistTracks(playlistId).map { it.toDomain() }
    }

    private suspend fun <T> withRefresh(block: suspend () -> T): T {
        return try {
            block()
        } catch (e: HttpException) {
            if (e.code() == 401 && refreshSession()) {
                block()
            } else {
                throw e
            }
        }
    }
}

private fun com.jellydj.mobile.core.network.MobileTrackDto.toDomain(): Track {
    return Track(
        id = id,
        title = title,
        artist = artist,
        album = album,
        durationMs = duration_ms,
        streamUrl = stream_url,
        jellyfinItemId = id,
        imageUrl = image_url
    )
}
