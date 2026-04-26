package com.jellydj.mobile.home

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Album
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.QueueMusic
import androidx.compose.material.icons.filled.Shuffle
import androidx.compose.material.icons.filled.TrendingUp
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import coil.request.ImageRequest
import com.jellydj.mobile.AppContainer
import com.jellydj.mobile.core.model.LibraryAlbum
import com.jellydj.mobile.core.model.Playlist
import com.jellydj.mobile.core.model.Track
import com.jellydj.mobile.player.AlbumArt
import com.jellydj.mobile.player.PlayerViewModel
import com.jellydj.mobile.player.SmartShuffleEngine
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.launch
import retrofit2.HttpException
import java.util.Calendar

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeScreen(
    container: AppContainer,
    playerViewModel: PlayerViewModel,
    onSessionInvalid: () -> Unit,
    onMenuOpen: () -> Unit,
    onPlaylistClick: (id: String, name: String, coverImageUrl: String?) -> Unit,
    onAlbumClick: (artistName: String, albumName: String) -> Unit = { _, _ -> }
) {
    val scope = rememberCoroutineScope()
    val recentTracks = remember { mutableStateListOf<Track>() }
    val globalTracks = remember { mutableStateListOf<Track>() }
    val playlists = remember { mutableStateListOf<Playlist>() }
    val recentAlbums = remember { mutableStateListOf<LibraryAlbum>() }
    val suggestedAlbums = remember { mutableStateListOf<LibraryAlbum>() }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    val session = container.sessionStore.read()
    val username = session?.username ?: ""

    LaunchedEffect(Unit) {
        coroutineScope {
            val recentDef = async { runCatching { container.libraryRepository.recentlyPlayed() } }
            val globalDef = async { runCatching { container.libraryRepository.topGlobalTracks(5) } }
            val playlistsDef = async { runCatching { container.libraryRepository.playlists() } }
            val recentAlbumsDef = async { runCatching { container.libraryRepository.recentAlbums(12) } }
            val suggestedDef = async { runCatching { container.libraryRepository.suggestedAlbums(8) } }

            recentDef.await()
                .onSuccess { recentTracks.addAll(it) }
                .onFailure { t ->
                    if ((t as? HttpException)?.code() == 401) { onSessionInvalid(); return@coroutineScope }
                    error = "Could not load library"
                }
            globalDef.await().onSuccess { globalTracks.addAll(it) }
            playlistsDef.await().onSuccess { playlists.addAll(it) }
            recentAlbumsDef.await().onSuccess { items ->
                recentAlbums.addAll(items.filter { it.affinityScore > 0f || it.trackCount > 0 })
            }
            suggestedDef.await().onSuccess { suggestedAlbums.addAll(it) }
        }
        loading = false
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("JellyDJ", fontWeight = FontWeight.Bold) },
                navigationIcon = {
                    IconButton(onClick = onMenuOpen) {
                        Icon(Icons.Default.Menu, contentDescription = "Menu")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = Color.Transparent)
            )
        },
        containerColor = MaterialTheme.colorScheme.background
    ) { padding ->
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(padding),
            contentPadding = PaddingValues(bottom = 24.dp)
        ) {
            // Greeting
            item {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(100.dp)
                        .background(
                            Brush.verticalGradient(
                                colors = listOf(
                                    MaterialTheme.colorScheme.primary.copy(alpha = 0.25f),
                                    Color.Transparent
                                )
                            )
                        )
                        .padding(horizontal = 20.dp, vertical = 16.dp)
                ) {
                    Column {
                        Text(
                            text = greeting(),
                            style = MaterialTheme.typography.headlineMedium,
                            fontWeight = FontWeight.Bold,
                            color = MaterialTheme.colorScheme.onBackground
                        )
                        if (username.isNotBlank()) {
                            Text(
                                text = username,
                                style = MaterialTheme.typography.titleMedium,
                                color = MaterialTheme.colorScheme.primary
                            )
                        }
                    }
                }
            }

            // Quick actions
            item {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 8.dp),
                    horizontalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    Button(
                        onClick = {
                            scope.launch {
                                val shuffled = SmartShuffleEngine().shuffle(recentTracks.toList())
                                if (shuffled.isNotEmpty()) playerViewModel.playQueue(shuffled, 0)
                            }
                        },
                        enabled = recentTracks.isNotEmpty(),
                        modifier = Modifier.weight(1f),
                        colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.primary)
                    ) {
                        Icon(Icons.Default.Shuffle, contentDescription = null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(6.dp))
                        Text("Shuffle")
                    }
                    FilledTonalButton(
                        onClick = {
                            if (recentTracks.isNotEmpty()) playerViewModel.playQueue(recentTracks.toList(), 0)
                        },
                        enabled = recentTracks.isNotEmpty(),
                        modifier = Modifier.weight(1f)
                    ) {
                        Icon(Icons.Default.PlayArrow, contentDescription = null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(6.dp))
                        Text("Play Recent")
                    }
                }
            }

            // Loading / error
            if (loading) {
                item {
                    Row(
                        modifier = Modifier.padding(20.dp),
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        CircularProgressIndicator(modifier = Modifier.size(20.dp))
                        Text("Loading your library...")
                    }
                }
            }
            if (error != null) {
                item {
                    Text(
                        text = error ?: "",
                        color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.padding(horizontal = 20.dp)
                    )
                }
            }

            // Recently Played Albums
            if (recentAlbums.isNotEmpty()) {
                item { SectionHeader("Recently Played") }
                item {
                    LazyRow(
                        contentPadding = PaddingValues(horizontal = 16.dp),
                        horizontalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        items(recentAlbums) { album ->
                            AlbumCard(album = album, onClick = { onAlbumClick(album.artist, album.name) })
                        }
                    }
                    Spacer(Modifier.height(4.dp))
                }
            }

            // Trending Now (top 5 global)
            if (globalTracks.isNotEmpty()) {
                item {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(start = 20.dp, end = 8.dp, top = 16.dp, bottom = 8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Icon(
                            Icons.Default.TrendingUp,
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.primary,
                            modifier = Modifier.size(20.dp)
                        )
                        Spacer(Modifier.width(6.dp))
                        Text(
                            "Trending Now",
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                            modifier = Modifier.weight(1f)
                        )
                    }
                }
                item {
                    LazyRow(
                        contentPadding = PaddingValues(horizontal = 16.dp),
                        horizontalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        itemsIndexed(globalTracks) { index, track ->
                            TopPickCard(
                                track = track,
                                rank = index + 1,
                                onClick = { scope.launch { playerViewModel.playQueue(globalTracks.toList(), index) } }
                            )
                        }
                    }
                    Spacer(Modifier.height(4.dp))
                }
            }

            // Suggested For You
            if (suggestedAlbums.isNotEmpty()) {
                item { SectionHeader("Suggested For You") }
                item {
                    LazyRow(
                        contentPadding = PaddingValues(horizontal = 16.dp),
                        horizontalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        items(suggestedAlbums) { album ->
                            AlbumCard(album = album, onClick = { onAlbumClick(album.artist, album.name) })
                        }
                    }
                    Spacer(Modifier.height(4.dp))
                }
            }

            // Your Playlists
            if (playlists.isNotEmpty()) {
                item { SectionHeader("Your Playlists") }
                item {
                    LazyRow(
                        contentPadding = PaddingValues(horizontal = 16.dp),
                        horizontalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        items(playlists) { playlist ->
                            PlaylistCard(
                                playlist = playlist,
                                onClick = { onPlaylistClick(playlist.id, playlist.name, playlist.coverImageUrl) }
                            )
                        }
                    }
                    Spacer(Modifier.height(4.dp))
                }
            }

            // Recently Played Tracks
            if (recentTracks.isNotEmpty()) {
                item {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(start = 20.dp, end = 8.dp, top = 16.dp, bottom = 8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(
                            "Recently Played",
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                            modifier = Modifier.weight(1f)
                        )
                        TextButton(onClick = {}) { /* no-op; user taps play to start */ }
                    }
                }
                itemsIndexed(recentTracks.take(12)) { index, track ->
                    TrackListItem(
                        track = track,
                        onPlay = { scope.launch { playerViewModel.playQueue(recentTracks.toList(), index) } }
                    )
                }
            }
        }
    }
}

@Composable
private fun SectionHeader(title: String) {
    Text(
        text = title,
        style = MaterialTheme.typography.titleMedium,
        fontWeight = FontWeight.Bold,
        modifier = Modifier.padding(start = 20.dp, end = 20.dp, top = 16.dp, bottom = 8.dp)
    )
}

@Composable
private fun AlbumCard(album: LibraryAlbum, onClick: () -> Unit) {
    Column(
        modifier = Modifier
            .width(120.dp)
            .clickable(onClick = onClick),
        horizontalAlignment = Alignment.Start
    ) {
        Box(
            modifier = Modifier
                .size(120.dp)
                .shadow(elevation = 3.dp, shape = RoundedCornerShape(10.dp))
                .clip(RoundedCornerShape(10.dp))
                .background(MaterialTheme.colorScheme.surfaceVariant),
            contentAlignment = Alignment.Center
        ) {
            if (!album.imageUrl.isNullOrBlank()) {
                AsyncImage(
                    model = ImageRequest.Builder(LocalContext.current)
                        .data(album.imageUrl)
                        .crossfade(true)
                        .build(),
                    contentDescription = null,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize()
                )
            } else {
                Icon(
                    Icons.Default.Album,
                    contentDescription = null,
                    modifier = Modifier.size(40.dp),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
        Spacer(Modifier.height(6.dp))
        Text(
            text = album.name,
            style = MaterialTheme.typography.labelMedium,
            fontWeight = FontWeight.SemiBold,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis
        )
        Text(
            text = album.artist,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis
        )
    }
}

@Composable
private fun TopPickCard(track: Track, rank: Int, onClick: () -> Unit) {
    Card(
        onClick = onClick,
        modifier = Modifier.size(150.dp),
        shape = RoundedCornerShape(12.dp),
        elevation = CardDefaults.cardElevation(defaultElevation = 4.dp)
    ) {
        Box(modifier = Modifier.fillMaxSize()) {
            AlbumArt(artworkUri = track.imageUrl, modifier = Modifier.fillMaxSize())
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(70.dp)
                    .align(Alignment.BottomCenter)
                    .background(
                        Brush.verticalGradient(
                            colors = listOf(Color.Transparent, Color.Black.copy(alpha = 0.80f))
                        )
                    )
            )
            // Rank badge
            Surface(
                modifier = Modifier
                    .padding(8.dp)
                    .align(Alignment.TopStart),
                shape = RoundedCornerShape(6.dp),
                color = MaterialTheme.colorScheme.primary.copy(alpha = 0.85f)
            ) {
                Text(
                    text = "#$rank",
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.Bold,
                    color = Color.White,
                    modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp)
                )
            }
            Column(
                modifier = Modifier
                    .align(Alignment.BottomStart)
                    .padding(10.dp)
            ) {
                Text(
                    text = track.title,
                    style = MaterialTheme.typography.labelLarge,
                    fontWeight = FontWeight.SemiBold,
                    color = Color.White,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
                Text(
                    text = track.artist,
                    style = MaterialTheme.typography.labelSmall,
                    color = Color.White.copy(alpha = 0.85f),
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
            }
        }
    }
}

@Composable
private fun PlaylistCard(playlist: Playlist, onClick: () -> Unit) {
    Column(
        modifier = Modifier
            .width(128.dp)
            .clickable(onClick = onClick),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Box(
            modifier = Modifier
                .size(128.dp)
                .shadow(elevation = 4.dp, shape = RoundedCornerShape(12.dp))
                .clip(RoundedCornerShape(12.dp))
        ) {
            if (playlist.coverImageUrl != null) {
                AsyncImage(
                    model = ImageRequest.Builder(LocalContext.current)
                        .data(playlist.coverImageUrl)
                        .crossfade(true)
                        .build(),
                    contentDescription = null,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize()
                )
            } else {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.secondaryContainer
                ) {
                    Box(contentAlignment = Alignment.Center) {
                        Icon(
                            Icons.Default.QueueMusic,
                            contentDescription = null,
                            modifier = Modifier.size(48.dp),
                            tint = MaterialTheme.colorScheme.onSecondaryContainer
                        )
                    }
                }
            }
        }
        Spacer(Modifier.height(8.dp))
        Text(
            text = playlist.name,
            style = MaterialTheme.typography.labelMedium,
            fontWeight = FontWeight.Medium,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(horizontal = 4.dp)
        )
        Text(
            text = "${playlist.trackCount} tracks",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1
        )
    }
}

@Composable
internal fun TrackListItem(track: Track, onPlay: () -> Unit) {
    var showInfo by remember { mutableStateOf(false) }
    ListItem(
        headlineContent = {
            Text(
                track.title,
                maxLines = 1,
                modifier = Modifier.clickable(onClick = onPlay)
            )
        },
        supportingContent = { Text("${track.artist} • ${track.album}", maxLines = 1) },
        leadingContent = {
            AlbumArt(
                artworkUri = track.imageUrl,
                modifier = Modifier
                    .size(48.dp)
                    .clip(RoundedCornerShape(6.dp))
                    .clickable(onClick = onPlay)
            )
        },
        trailingContent = {
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onPlay) {
                    Icon(Icons.Default.PlayArrow, contentDescription = "Play")
                }
                Box {
                    IconButton(onClick = { showInfo = true }) {
                        Icon(Icons.Default.MoreVert, contentDescription = "Track info")
                    }
                    TrackInfoDropdown(
                        track = track,
                        expanded = showInfo,
                        onDismiss = { showInfo = false }
                    )
                }
            }
        }
    )
}

private fun greeting(): String {
    val hour = Calendar.getInstance().get(Calendar.HOUR_OF_DAY)
    return when {
        hour < 5  -> "Good night,"
        hour < 12 -> "Good morning,"
        hour < 17 -> "Good afternoon,"
        hour < 21 -> "Good evening,"
        else      -> "Good night,"
    }
}

@Composable
private fun TrackInfoDropdown(track: Track, expanded: Boolean, onDismiss: () -> Unit) {
    DropdownMenu(expanded = expanded, onDismissRequest = onDismiss) {
        // Track name header
        DropdownMenuItem(
            text = {
                Text(
                    track.title,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
            },
            onClick = onDismiss,
            enabled = false
        )
        HorizontalDivider()
        TrackInfoRow("Duration", formatDuration(track.durationMs))
        if (track.playCount > 0 || track.artistAffinity != null) {
            TrackInfoRow("Plays", track.playCount.toString())
        }
        track.artistAffinity?.let { TrackInfoRow("Affinity", it.toInt().toString()) }
        track.globalPopularity?.let { TrackInfoRow("Global Pop.", it.toInt().toString()) }
        track.bpm?.let { TrackInfoRow("BPM", it.toString()) }
        track.energy?.let { TrackInfoRow("Energy", "${(it * 100).toInt()}%") }
    }
}

@Composable
private fun TrackInfoRow(label: String, value: String) {
    DropdownMenuItem(
        text = {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text(label, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Spacer(Modifier.width(24.dp))
                Text(value, fontWeight = FontWeight.SemiBold)
            }
        },
        onClick = {}
    )
}

private fun formatDuration(ms: Long): String {
    val totalSeconds = ms / 1000
    val minutes = totalSeconds / 60
    val seconds = totalSeconds % 60
    return "$minutes:${seconds.toString().padStart(2, '0')}"
}
