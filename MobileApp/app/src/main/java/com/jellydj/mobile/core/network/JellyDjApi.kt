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
    val refresh_token: String
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
}
