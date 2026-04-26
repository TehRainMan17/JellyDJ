package com.jellydj.mobile.core.model

data class Track(
    val id: String,
    val title: String,
    val artist: String,
    val album: String,
    val durationMs: Long,
    val streamUrl: String,
    val jellyfinItemId: String,
    val imageUrl: String? = null,
    val bpm: Int? = null,
    val energy: Float? = null,
    val mood: String? = null,
    val artistAffinity: Float? = null,
    val globalPopularity: Float? = null,
    val playCount: Int = 0
)

data class Artist(
    val id: String,
    val name: String,
    val imageUrl: String? = null
)

data class Playlist(
    val id: String,
    val name: String,
    val owner: String,
    val trackCount: Int,
    val isCollaborative: Boolean = false,
    val coverImageUrl: String? = null
)

data class LibraryArtist(
    val id: String,
    val name: String,
    val imageUrl: String? = null,
    val affinityScore: Float,
    val globalPopularity: Float? = null,
    val trackCount: Int
)

data class LibraryAlbum(
    val id: String,
    val name: String,
    val artist: String,
    val imageUrl: String? = null,
    val affinityScore: Float,
    val globalPopularity: Float? = null,
    val trackCount: Int
)

data class LibraryGenre(
    val id: String,
    val name: String,
    val affinityScore: Float,
    val trackCount: Int
)

data class RelatedArtist(val name: String, val matchScore: Double)

data class GenreWeight(val genre: String, val weight: Double)

data class ArtistDetail(
    val id: String,
    val name: String,
    val imageUrl: String? = null,
    val affinityScore: Float,
    val globalPopularity: Float? = null,
    val trendDirection: String? = null,
    val biography: String? = null,
    val canonicalGenres: List<GenreWeight> = emptyList(),
    val relatedArtists: List<RelatedArtist> = emptyList()
)

data class LibraryYear(val year: Int, val trackCount: Int)

data class SmartCollection(
    val key: String,
    val label: String,
    val description: String,
    val iconHint: String
)
