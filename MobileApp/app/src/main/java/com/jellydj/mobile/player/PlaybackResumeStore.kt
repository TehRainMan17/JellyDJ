package com.jellydj.mobile.player

import android.content.Context
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import org.json.JSONArray
import org.json.JSONObject

class PlaybackResumeStore(context: Context) {
    private val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun save(items: List<MediaItem>, index: Int, positionMs: Long) {
        val array = JSONArray()
        items.forEach { item ->
            val obj = JSONObject()
                .put("id", item.mediaId)
                .put("uri", item.localConfiguration?.uri?.toString() ?: "")
                .put("title", item.mediaMetadata.title?.toString() ?: "")
                .put("artist", item.mediaMetadata.artist?.toString() ?: "")
                .put("album", item.mediaMetadata.albumTitle?.toString() ?: "")
            array.put(obj)
        }

        prefs.edit()
            .putString(KEY_ITEMS, array.toString())
            .putInt(KEY_INDEX, index)
            .putLong(KEY_POSITION_MS, positionMs)
            .apply()
    }

    fun load(): ResumeState? {
        val raw = prefs.getString(KEY_ITEMS, null) ?: return null
        val index = prefs.getInt(KEY_INDEX, 0)
        val positionMs = prefs.getLong(KEY_POSITION_MS, 0L)

        val parsed = JSONArray(raw)
        val items = mutableListOf<MediaItem>()
        for (i in 0 until parsed.length()) {
            val obj = parsed.getJSONObject(i)
            val uri = obj.optString("uri", "")
            if (uri.isBlank()) continue

            items += MediaItem.Builder()
                .setMediaId(obj.optString("id", "item-$i"))
                .setUri(uri)
                .setMediaMetadata(
                    MediaMetadata.Builder()
                        .setTitle(obj.optString("title", ""))
                        .setArtist(obj.optString("artist", ""))
                        .setAlbumTitle(obj.optString("album", ""))
                        .setIsPlayable(true)
                        .build()
                )
                .build()
        }

        if (items.isEmpty()) return null

        val safeIndex = index.coerceIn(0, items.lastIndex)
        return ResumeState(items = items, index = safeIndex, positionMs = positionMs)
    }

    data class ResumeState(
        val items: List<MediaItem>,
        val index: Int,
        val positionMs: Long
    )

    companion object {
        private const val PREFS = "jellydj_playback_resume"
        private const val KEY_ITEMS = "items"
        private const val KEY_INDEX = "index"
        private const val KEY_POSITION_MS = "position_ms"
    }
}
