package com.jellydj.mobile.search

import com.jellydj.mobile.core.model.Artist
import com.jellydj.mobile.core.model.Track
import com.jellydj.mobile.core.network.JellyDjApi
import retrofit2.HttpException

interface SearchRepository {
    suspend fun search(query: String): SearchResult
}

data class SearchResult(
    val tracks: List<Track>,
    val artists: List<Artist>
)

class JellyDjSearchRepository(
    private val api: JellyDjApi,
    private val refreshSession: suspend () -> Boolean
) : SearchRepository {

    override suspend fun search(query: String): SearchResult {
        if (query.isBlank()) return SearchResult(emptyList(), emptyList())

        val response = withRefresh {
            api.search(query = query)
        }

        val tracks = response.tracks.map {
            Track(
                id = it.id,
                title = it.title,
                artist = it.artist,
                album = it.album,
                durationMs = it.duration_ms,
                streamUrl = it.stream_url,
                jellyfinItemId = it.id,
                imageUrl = it.image_url
            )
        }

        val artists = tracks.map { it.artist }
            .distinct()
            .mapIndexed { index, name ->
                Artist(id = "artist-$index-$name", name = name)
            }

        return SearchResult(tracks = tracks, artists = artists)
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
