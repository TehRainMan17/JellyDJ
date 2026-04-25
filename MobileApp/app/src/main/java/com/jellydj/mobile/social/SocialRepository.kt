package com.jellydj.mobile.social

interface SocialRepository {
    suspend fun leaderboard(): List<LeaderboardEntry>
}

data class LeaderboardEntry(
    val userName: String,
    val points: Int,
    val streakDays: Int
)

class FakeSocialRepository : SocialRepository {
    override suspend fun leaderboard(): List<LeaderboardEntry> = listOf(
        LeaderboardEntry("Ray", 12400, 45),
        LeaderboardEntry("Ari", 11780, 38),
        LeaderboardEntry("Mina", 10490, 32)
    )
}