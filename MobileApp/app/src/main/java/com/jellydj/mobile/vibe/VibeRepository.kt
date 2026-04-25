package com.jellydj.mobile.vibe

interface VibeRepository {
    suspend fun suggest(seed: VibeSeed): VibeRecommendation
}

data class VibeSeed(
    val vibe: String,
    val minEnergy: Float,
    val maxEnergy: Float,
    val preferredGenres: List<String>
)

data class VibeRecommendation(
    val title: String,
    val explanation: String,
    val trackIds: List<String>
)

class FakeVibeRepository : VibeRepository {
    override suspend fun suggest(seed: VibeSeed): VibeRecommendation {
        return VibeRecommendation(
            title = "${seed.vibe.replaceFirstChar { it.uppercase() }} Booster",
            explanation = "Seeded by your recent listening and current vibe preference.",
            trackIds = listOf("jf_1", "jf_2", "jf_3")
        )
    }
}