package com.jellydj.mobile.library

import com.jellydj.mobile.core.model.ArtistDetail
import com.jellydj.mobile.core.model.GenreWeight
import com.jellydj.mobile.core.model.LibraryAlbum
import com.jellydj.mobile.core.model.LibraryArtist
import com.jellydj.mobile.core.model.LibraryGenre
import com.jellydj.mobile.core.model.LibraryYear
import com.jellydj.mobile.core.model.Playlist
import com.jellydj.mobile.core.model.RelatedArtist
import com.jellydj.mobile.core.model.SmartCollection
import com.jellydj.mobile.core.model.Track
import com.jellydj.mobile.core.network.JellyDjApi
import retrofit2.HttpException

interface LibraryRepository {
    suspend fun recentlyPlayed(): List<Track>
    suspend fun topTracks(): List<Track>
    suspend fun playlists(): List<Playlist>
    suspend fun playlistTracks(playlistId: String): List<Track>
    suspend fun libraryArtists(query: String = "", limit: Int = 200): List<LibraryArtist>
    suspend fun libraryAlbums(query: String = "", artist: String? = null, sort: String = "affinity", limit: Int = 200): List<LibraryAlbum>
    suspend fun recentAlbums(limit: Int = 10): List<LibraryAlbum>
    suspend fun suggestedAlbums(limit: Int = 8): List<LibraryAlbum>
    suspend fun topGlobalTracks(limit: Int = 5): List<Track>
    suspend fun libraryGenres(query: String = ""): List<LibraryGenre>
    suspend fun libraryTracks(
        query: String = "",
        artist: String? = null,
        album: String? = null,
        genre: String? = null,
        sort: String = "personal"
    ): List<Track>
    suspend fun artistTracks(artistName: String, sort: String = "personal", query: String = ""): List<Track>
    suspend fun artistDetail(artistName: String): ArtistDetail
    suspend fun libraryYears(): List<LibraryYear>
    suspend fun yearTracks(year: Int, sort: String = "personal"): List<Track>
    suspend fun smartCollections(): List<SmartCollection>
    suspend fun smartCollectionTracks(key: String): List<Track>
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

    override suspend fun libraryArtists(query: String, limit: Int): List<LibraryArtist> = withRefresh {
        api.libraryArtists(query = query.takeIf { it.isNotBlank() }, limit = limit).map {
            LibraryArtist(
                id = it.id,
                name = it.name,
                imageUrl = it.image_url,
                affinityScore = it.affinity_score,
                globalPopularity = it.global_popularity,
                trackCount = it.track_count
            )
        }
    }

    override suspend fun libraryAlbums(query: String, artist: String?, sort: String, limit: Int): List<LibraryAlbum> = withRefresh {
        api.libraryAlbums(
            query = query.takeIf { it.isNotBlank() },
            artist = artist,
            sort = sort,
            limit = limit
        ).map {
            LibraryAlbum(
                id = it.id,
                name = it.name,
                artist = it.artist,
                imageUrl = it.image_url,
                affinityScore = it.affinity_score,
                globalPopularity = it.global_popularity,
                trackCount = it.track_count
            )
        }
    }

    override suspend fun recentAlbums(limit: Int): List<LibraryAlbum> = withRefresh {
        api.libraryAlbums(sort = "recent", limit = limit).map {
            LibraryAlbum(id = it.id, name = it.name, artist = it.artist, imageUrl = it.image_url,
                affinityScore = it.affinity_score, globalPopularity = it.global_popularity, trackCount = it.track_count)
        }
    }

    override suspend fun suggestedAlbums(limit: Int): List<LibraryAlbum> = withRefresh {
        api.libraryAlbums(sort = "affinity", limit = limit).map {
            LibraryAlbum(id = it.id, name = it.name, artist = it.artist, imageUrl = it.image_url,
                affinityScore = it.affinity_score, globalPopularity = it.global_popularity, trackCount = it.track_count)
        }
    }

    override suspend fun topGlobalTracks(limit: Int): List<Track> = withRefresh {
        api.libraryTracks(sort = "global", limit = limit).map { it.toDomain() }
    }

    override suspend fun libraryGenres(query: String): List<LibraryGenre> = withRefresh {
        api.libraryGenres(query = query.takeIf { it.isNotBlank() }).map {
            LibraryGenre(
                id = it.id,
                name = it.name,
                affinityScore = it.affinity_score,
                trackCount = it.track_count
            )
        }
    }

    override suspend fun libraryTracks(
        query: String,
        artist: String?,
        album: String?,
        genre: String?,
        sort: String
    ): List<Track> = withRefresh {
        api.libraryTracks(
            query = query.takeIf { it.isNotBlank() },
            artist = artist,
            album = album,
            genre = genre,
            sort = sort
        ).map { it.toDomain() }
    }

    override suspend fun artistTracks(artistName: String, sort: String, query: String): List<Track> = withRefresh {
        api.libraryArtistTracks(
            artistName = artistName,
            sort = sort,
            query = query.takeIf { it.isNotBlank() }
        ).map { it.toDomain() }
    }

    override suspend fun artistDetail(artistName: String): ArtistDetail = withRefresh {
        val dto = api.artistDetail(artistName)
        ArtistDetail(
            id = dto.name,
            name = dto.name,
            imageUrl = dto.image_url,
            affinityScore = dto.affinity_score,
            globalPopularity = dto.global_popularity,
            trendDirection = dto.trend_direction,
            biography = dto.biography,
            canonicalGenres = dto.canonical_genres.map { GenreWeight(it.genre, it.weight) },
            relatedArtists = dto.related_artists.map { RelatedArtist(it.name, it.match_score) }
        )
    }

    override suspend fun libraryYears(): List<LibraryYear> = withRefresh {
        api.libraryYears().map { LibraryYear(it.year, it.track_count) }
    }

    override suspend fun yearTracks(year: Int, sort: String): List<Track> = withRefresh {
        api.yearTracks(year = year, sort = sort).map { it.toDomain() }
    }

    override suspend fun smartCollections(): List<SmartCollection> = withRefresh {
        api.smartCollections().map {
            SmartCollection(key = it.key, label = it.label, description = it.description, iconHint = it.icon_hint)
        }
    }

    override suspend fun smartCollectionTracks(key: String): List<Track> = withRefresh {
        api.smartCollectionTracks(key).map { it.toDomain() }
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

private fun com.jellydj.mobile.core.network.MobileLibraryTrackDto.toDomain(): Track {
    return Track(
        id = id,
        title = title,
        artist = artist,
        album = album,
        durationMs = duration_ms,
        streamUrl = stream_url,
        jellyfinItemId = id,
        imageUrl = image_url,
        artistAffinity = artist_affinity,
        globalPopularity = global_popularity,
        playCount = play_count,
        bpm = bpm?.toInt(),
        energy = energy
    )
}
