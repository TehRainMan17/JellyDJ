package com.jellydj.mobile.player

import com.jellydj.mobile.core.model.Track
import kotlin.math.abs

/**
 * Keeps playback varied by discouraging back-to-back tracks from the same artist,
 * album, and similar energy levels.
 */
class SmartShuffleEngine {
    fun shuffle(tracks: List<Track>, seed: Long = System.nanoTime()): List<Track> {
        if (tracks.size < 3) return tracks.shuffled(kotlin.random.Random(seed))

        val random = kotlin.random.Random(seed)
        val pool = tracks.toMutableList()
        val result = mutableListOf<Track>()

        var previous: Track? = null
        while (pool.isNotEmpty()) {
            val candidate = pool.maxByOrNull { track ->
                scoreCandidate(track, previous, random)
            } ?: pool.first()

            result += candidate
            pool.remove(candidate)
            previous = candidate
        }

        return result
    }

    private fun scoreCandidate(track: Track, previous: Track?, random: kotlin.random.Random): Double {
        if (previous == null) return random.nextDouble(0.0, 1.0)

        var score = random.nextDouble(0.0, 0.1)

        if (track.artist != previous.artist) score += 0.45
        if (track.album != previous.album) score += 0.25

        val currentEnergy = track.energy
        val previousEnergy = previous.energy
        if (currentEnergy != null && previousEnergy != null) {
            score += (1.0 - abs(currentEnergy - previousEnergy)) * 0.15
        } else {
            score += 0.08
        }

        if (track.mood != null && previous.mood != null && track.mood != previous.mood) {
            score += 0.07
        }

        return score
    }
}