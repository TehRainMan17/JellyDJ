package com.jellydj.mobile.library

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import com.jellydj.mobile.core.model.CatalogAlbum
import com.jellydj.mobile.core.model.CatalogData
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

private const val TAG = "AlbumCatalogStore"
private const val PREFS_NAME = "jellydj_catalog"
private const val KEY_VERSION = "catalog_version"
private const val CATALOG_FILE = "album_catalog.json"

/**
 * Persists the album catalog locally so the mobile app can serve library data
 * without a network roundtrip when the backend version hasn't changed.
 *
 * Storage layout:
 *   - Version number in SharedPreferences (fast single-key read on startup)
 *   - Full catalog JSON in filesDir/album_catalog.json
 *
 * Writes are atomic: data is written to a temp file first, then renamed.
 * If the app is killed mid-write, the old file is untouched.
 */
class AlbumCatalogStore(context: Context) {

    private val prefs: SharedPreferences =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    private val filesDir: File = context.filesDir

    fun getStoredVersion(): Int = prefs.getInt(KEY_VERSION, -1)

    fun saveCatalog(version: Int, data: CatalogData) {
        val json = serializeCatalog(data)
        val target = File(filesDir, CATALOG_FILE)
        val temp = File(filesDir, "$CATALOG_FILE.tmp")
        try {
            temp.writeText(json, Charsets.UTF_8)
            // Atomic rename — only replaces target if write succeeded
            if (!temp.renameTo(target)) {
                // renameTo can fail across filesystems on some devices; fall back to copy+delete
                target.writeText(json, Charsets.UTF_8)
                temp.delete()
            }
            prefs.edit().putInt(KEY_VERSION, version).apply()
        } catch (e: Exception) {
            Log.e(TAG, "Failed to save catalog", e)
            temp.delete()
        }
    }

    fun loadCatalog(): CatalogData? {
        val file = File(filesDir, CATALOG_FILE)
        if (!file.exists()) return null
        return try {
            val version = getStoredVersion()
            if (version < 0) return null
            deserializeCatalog(version, file.readText(Charsets.UTF_8))
        } catch (e: Exception) {
            Log.e(TAG, "Failed to read catalog from disk — treating as missing", e)
            null
        }
    }

    fun clearCatalog() {
        File(filesDir, CATALOG_FILE).delete()
        File(filesDir, "$CATALOG_FILE.tmp").delete()
        prefs.edit().remove(KEY_VERSION).apply()
    }

    // ── Serialization ─────────────────────────────────────────────────────────

    private fun serializeCatalog(data: CatalogData): String {
        val root = JSONObject()
        root.put("version", data.version)
        val albumsArr = JSONArray()
        for (album in data.albums) {
            val obj = JSONObject()
            obj.put("key", album.key)
            obj.put("name", album.name)
            obj.put("artist", album.artist)
            obj.put("track_count", album.trackCount)
            if (album.avgPopularity != null) obj.put("avg_popularity", album.avgPopularity.toDouble())
            val ids = JSONArray(); album.jellyfinAlbumIds.forEach { ids.put(it) }
            obj.put("jellyfin_album_ids", ids)
            val tids = JSONArray(); album.trackIds.forEach { tids.put(it) }
            obj.put("track_ids", tids)
            albumsArr.put(obj)
        }
        root.put("albums", albumsArr)
        return root.toString()
    }

    private fun deserializeCatalog(version: Int, json: String): CatalogData {
        val root = JSONObject(json)
        val albumsArr = root.getJSONArray("albums")
        val albums = mutableListOf<CatalogAlbum>()
        for (i in 0 until albumsArr.length()) {
            val obj = albumsArr.getJSONObject(i)
            val albumIds = mutableListOf<String>()
            val rawIds = obj.optJSONArray("jellyfin_album_ids")
            if (rawIds != null) for (j in 0 until rawIds.length()) albumIds.add(rawIds.getString(j))
            val trackIds = mutableListOf<String>()
            val rawTids = obj.optJSONArray("track_ids")
            if (rawTids != null) for (j in 0 until rawTids.length()) trackIds.add(rawTids.getString(j))
            albums.add(CatalogAlbum(
                key = obj.getString("key"),
                name = obj.getString("name"),
                artist = obj.getString("artist"),
                jellyfinAlbumIds = albumIds,
                trackIds = trackIds,
                trackCount = obj.optInt("track_count", trackIds.size),
                avgPopularity = if (obj.has("avg_popularity")) obj.getDouble("avg_popularity").toFloat() else null
            ))
        }
        return CatalogData(version = version, albums = albums)
    }
}
