package com.jellydj.mobile.core.network

import com.jellydj.mobile.BuildConfig
import com.jellydj.mobile.core.session.SessionStore
import com.squareup.moshi.Moshi
import com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import java.util.concurrent.TimeUnit
import retrofit2.Retrofit
import retrofit2.converter.moshi.MoshiConverterFactory

object JellyDjApiClientFactory {
    private val schemePrefix = Regex("^[a-zA-Z][a-zA-Z0-9+.-]*://")

    fun createClient(sessionStore: SessionStore): OkHttpClient {
        val authInterceptor = Interceptor { chain ->
            val request = chain.request()
            val token = sessionStore.read()?.accessToken

            val authenticatedRequest = if (token != null) {
                request.newBuilder()
                    .addHeader("Authorization", "Bearer $token")
                    .build()
            } else {
                request
            }

            chain.proceed(authenticatedRequest)
        }

        val baseUrlOverrideInterceptor = Interceptor { chain ->
            val request = chain.request()
            val configured = sessionStore.readServerBaseUrl().orEmpty().ifBlank { BuildConfig.MOBILE_API_BASE_URL }
            val normalized = if (schemePrefix.containsMatchIn(configured)) {
                configured
            } else {
                "https://$configured"
            }

            val targetBase = normalized.trimEnd('/').plus('/').toHttpUrlOrNull()
            if (targetBase == null) {
                throw IllegalStateException("Invalid configured server base URL: '$configured'")
            } else {
                val requestPath = request.url.encodedPath.removePrefix("/")
                val basePath = targetBase.encodedPath.trim('/').ifBlank { "" }
                val mergedPath = if (basePath.isBlank()) {
                    "/$requestPath"
                } else {
                    "/$basePath/$requestPath"
                }

                val newUrl = request.url.newBuilder()
                    .scheme(targetBase.scheme)
                    .host(targetBase.host)
                    .port(targetBase.port)
                    .encodedPath(mergedPath)
                    .build()

                chain.proceed(request.newBuilder().url(newUrl).build())
            }
        }

        val builder = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(15, TimeUnit.SECONDS)
            .addInterceptor(baseUrlOverrideInterceptor)
            .addInterceptor(authInterceptor)

        if (BuildConfig.DEBUG) {
            builder.addInterceptor(
                HttpLoggingInterceptor().apply { level = HttpLoggingInterceptor.Level.BASIC }
            )
        }

        return builder.build()
    }

    fun create(sessionStore: SessionStore): JellyDjApi = create(createClient(sessionStore))

    fun create(client: OkHttpClient): JellyDjApi {
        val moshi = Moshi.Builder()
            .add(KotlinJsonAdapterFactory())
            .build()

        return Retrofit.Builder()
            .baseUrl("https://placeholder.local/")
            .client(client)
            .addConverterFactory(MoshiConverterFactory.create(moshi))
            .build()
            .create(JellyDjApi::class.java)
    }
}
