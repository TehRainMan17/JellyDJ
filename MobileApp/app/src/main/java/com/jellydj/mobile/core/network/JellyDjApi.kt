package com.jellydj.mobile.core.network

import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Query
import retrofit2.http.Path

data class HealthResponse(
    val status: String,
    val service: String,
    val version: String
)

data class LoginRequest(
    val username: String,
    val password: String,
    val remember_me: Boolean = false
)

data class LoginResponse(
    val access_token: String,
    val refresh_token: String,
    val username: String,
    val is_admin: Boolean
)

data class RefreshRequest(
    val refresh_token: String
)

data class RefreshResponse(
    val access_token: String,
    val refresh_token: String? = null
)

data class MeResponse(
    val user_id: String,
    val username: String,
    val is_admin: Boolean
)

data class MobileTrackDto(
    val id: String,
    val title: String,
    val artist: String,
    val album: String,
    val duration_ms: Long,
    val stream_url: String,
    val image_url: String?
)

data class MobilePlaylistDto(
    val id: String,
    val name: String,
    val track_count: Int,
    val cover_image_url: String? = null
)

data class MobileSearchResponseDto(
    val tracks: List<MobileTrackDto>
)

data class MobileLibraryTrackDto(
    val id: String,
    val title: String,
    val artist: String,
    val album: String,
    val duration_ms: Long,
    val stream_url: String,
    val image_url: String?,
    val artist_affinity: Float,
    val global_popularity: Float?,
    val play_count: Int,
    val bpm: Double? = null,
    val energy: Float? = null
)

data class MobileLibraryArtistDto(
    val id: String,
    val name: String,
    val image_url: String?,
    val affinity_score: Float,
    val global_popularity: Float?,
    val track_count: Int
)

data class MobileLibraryAlbumDto(
    val id: String,
    val name: String,
    val artist: String,
    val image_url: String?,
    val affinity_score: Float,
    val global_popularity: Float?,
    val track_count: Int
)

data class MobileLibraryGenreDto(
    val id: String,
    val name: String,
    val affinity_score: Float,
    val track_count: Int
)

data class MobileRelatedArtistDto(
    val name: String,
    val match_score: Double
)

data class MobileGenreWeightDto(
    val genre: String,
    val weight: Double
)

data class MobileArtistDetailDto(
    val name: String,
    val image_url: String?,
    val affinity_score: Float,
    val global_popularity: Float?,
    val trend_direction: String?,
    val biography: String?,
    val canonical_genres: List<MobileGenreWeightDto>,
    val related_artists: List<MobileRelatedArtistDto>
)

data class MobileLibraryYearDto(
    val year: Int,
    val track_count: Int
)

data class MobileSmartCollectionDto(
    val key: String,
    val label: String,
    val description: String,
    val icon_hint: String
)

data class CatalogVersionDto(
    val version: Int,
    val updated_at: String?,
    val total_albums: Int,
    val total_tracks: Int
)

data class CatalogAlbumEntryDto(
    val key: String,
    val name: String,
    val artist: String,
    val jellyfin_album_ids: List<String>,
    val track_ids: List<String>,
    val track_count: Int,
    val avg_popularity: Float?
)

data class FullCatalogDto(
    val version: Int,
    val albums: List<CatalogAlbumEntryDto>
)

interface JellyDjApi {
    @GET("api/health")
    suspend fun health(): HealthResponse

    @POST("api/auth/login")
    suspend fun login(@Body body: LoginRequest): LoginResponse

    @POST("api/auth/refresh")
    suspend fun refresh(@Body body: RefreshRequest): RefreshResponse

    @GET("api/auth/me")
    suspend fun me(): MeResponse

    @GET("api/mobile/library/recent")
    suspend fun recent(@Query("limit") limit: Int = 100): List<MobileTrackDto>

    @GET("api/mobile/search")
    suspend fun search(
        @Query("q") query: String,
        @Query("limit") limit: Int = 50
    ): MobileSearchResponseDto

    @GET("api/mobile/playlists")
    suspend fun playlists(): List<MobilePlaylistDto>

    @GET("api/mobile/playlists/{playlistId}/tracks")
    suspend fun playlistTracks(@Path("playlistId") playlistId: String): List<MobileTrackDto>

    @GET("api/mobile/top-tracks")
    suspend fun topTracks(@Query("limit") limit: Int = 20): List<MobileTrackDto>

    @GET("api/mobile/library/artists")
    suspend fun libraryArtists(
        @Query("q") query: String? = null,
        @Query("limit") limit: Int = 200
    ): List<MobileLibraryArtistDto>

    @GET("api/mobile/library/albums")
    suspend fun libraryAlbums(
        @Query("q") query: String? = null,
        @Query("artist") artist: String? = null,
        @Query("sort") sort: String = "affinity",
        @Query("limit") limit: Int = 200
    ): List<MobileLibraryAlbumDto>

    @GET("api/mobile/library/genres")
    suspend fun libraryGenres(
        @Query("q") query: String? = null,
        @Query("limit") limit: Int = 200
    ): List<MobileLibraryGenreDto>

    @GET("api/mobile/library/tracks")
    suspend fun libraryTracks(
        @Query("q") query: String? = null,
        @Query("artist") artist: String? = null,
        @Query("album") album: String? = null,
        @Query("genre") genre: String? = null,
        @Query("sort") sort: String = "personal",
        @Query("limit") limit: Int = 250
    ): List<MobileLibraryTrackDto>

    @GET("api/mobile/library/artists/{artistName}/tracks")
    suspend fun libraryArtistTracks(
        @Path("artistName") artistName: String,
        @Query("sort") sort: String = "personal",
        @Query("q") query: String? = null,
        @Query("limit") limit: Int = 250
    ): List<MobileLibraryTrackDto>

    @GET("api/mobile/library/artists/{artistName}/detail")
    suspend fun artistDetail(
        @Path("artistName") artistName: String
    ): MobileArtistDetailDto

    @GET("api/mobile/library/years")
    suspend fun libraryYears(
        @Query("limit") limit: Int = 200
    ): List<MobileLibraryYearDto>

    @GET("api/mobile/library/years/{year}/tracks")
    suspend fun yearTracks(
        @Path("year") year: Int,
        @Query("sort") sort: String = "personal",
        @Query("limit") limit: Int = 500
    ): List<MobileLibraryTrackDto>

    @GET("api/mobile/library/smart")
    suspend fun smartCollections(): List<MobileSmartCollectionDto>

    @GET("api/mobile/library/smart/{collectionKey}/tracks")
    suspend fun smartCollectionTracks(
        @Path("collectionKey") collectionKey: String,
        @Query("limit") limit: Int = 100
    ): List<MobileLibraryTrackDto>

    @GET("api/mobile/catalog/version")
    suspend fun catalogVersion(): CatalogVersionDto

    @GET("api/mobile/catalog/full")
    suspend fun catalogFull(): FullCatalogDto
}
