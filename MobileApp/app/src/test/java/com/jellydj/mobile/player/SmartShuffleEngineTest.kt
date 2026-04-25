package com.jellydj.mobile.player

import com.jellydj.mobile.core.model.Track
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SmartShuffleEngineTest {

    private val sample = listOf(
        Track("1", "A1", "ArtistA", "Album1", 180_000, "url://1", "jf1", energy = 0.2f),
        Track("2", "A2", "ArtistA", "Album2", 180_000, "url://2", "jf2", energy = 0.4f),
        Track("3", "B1", "ArtistB", "Album1", 180_000, "url://3", "jf3", energy = 0.7f),
        Track("4", "C1", "ArtistC", "Album3", 180_000, "url://4", "jf4", energy = 0.5f),
        Track("5", "D1", "ArtistD", "Album4", 180_000, "url://5", "jf5", energy = 0.8f)
    )

    @Test
    fun shuffle_keepsSameSizeAndItems() {
        val shuffled = SmartShuffleEngine().shuffle(sample, seed = 42L)
        assertTrue(shuffled.size == sample.size)
        assertTrue(shuffled.map { it.id }.toSet() == sample.map { it.id }.toSet())
    }

    @Test
    fun shuffle_isNotOriginalOrderWithStableSeed() {
        val shuffled = SmartShuffleEngine().shuffle(sample, seed = 42L)
        assertNotEquals(sample.map { it.id }, shuffled.map { it.id })
    }
}