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
    val mood: String? = null
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
