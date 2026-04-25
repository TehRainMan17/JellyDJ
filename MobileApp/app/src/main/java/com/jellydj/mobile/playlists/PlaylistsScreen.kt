package com.jellydj.mobile.playlists

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.QueueMusic
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
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
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import coil.request.ImageRequest
import com.jellydj.mobile.AppContainer
import com.jellydj.mobile.core.model.Playlist
import com.jellydj.mobile.player.PlayerViewModel
import com.jellydj.mobile.player.SmartShuffleEngine
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PlaylistsScreen(
    container: AppContainer,
    playerViewModel: PlayerViewModel,
    onPlaylistClick: (id: String, name: String, coverImageUrl: String?) -> Unit
) {
    val scope = rememberCoroutineScope()
    val playlists = remember { mutableStateListOf<Playlist>() }
    var loading by remember { mutableStateOf(true) }

    LaunchedEffect(Unit) {
        runCatching {
            playlists.clear()
            playlists.addAll(container.libraryRepository.playlists())
        }
        loading = false
    }

    Scaffold(
        topBar = {
            TopAppBar(title = { Text("Playlists") })
        }
    ) { padding ->
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
            contentPadding = PaddingValues(bottom = 16.dp),
            verticalArrangement = Arrangement.spacedBy(2.dp)
        ) {
            if (loading) {
                item {
                    Row(
                        modifier = Modifier.padding(16.dp),
                        horizontalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        CircularProgressIndicator(modifier = Modifier.size(20.dp))
                        Text("Loading playlists...")
                    }
                }
            }

            items(playlists) { playlist ->
                ListItem(
                    modifier = Modifier.clickable { onPlaylistClick(playlist.id, playlist.name, playlist.coverImageUrl) },
                    headlineContent = {
                        Text(
                            playlist.name,
                            maxLines = 1,
                            style = MaterialTheme.typography.bodyLarge
                        )
                    },
                    supportingContent = {
                        Text(
                            "${playlist.trackCount} track${if (playlist.trackCount != 1) "s" else ""}",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    },
                    leadingContent = {
                        PlaylistThumbnail(
                            coverImageUrl = playlist.coverImageUrl,
                            size = 54
                        )
                    },
                    trailingContent = {
                        IconButton(onClick = {
                            scope.launch {
                                runCatching {
                                    val tracks = container.libraryRepository.playlistTracks(playlist.id)
                                    if (tracks.isNotEmpty()) {
                                        playerViewModel.playQueue(SmartShuffleEngine().shuffle(tracks), 0)
                                    }
                                }
                            }
                        }) {
                            Icon(
                                Icons.Default.PlayArrow,
                                contentDescription = "Play",
                                tint = MaterialTheme.colorScheme.primary
                            )
                        }
                    }
                )
            }
        }
    }
}

@Composable
fun PlaylistThumbnail(coverImageUrl: String?, size: Int) {
    val shape = RoundedCornerShape(8.dp)
    if (coverImageUrl != null) {
        AsyncImage(
            model = ImageRequest.Builder(LocalContext.current)
                .data(coverImageUrl)
                .crossfade(true)
                .build(),
            contentDescription = null,
            contentScale = ContentScale.Crop,
            modifier = Modifier
                .size(size.dp)
                .clip(shape)
        )
    } else {
        Surface(
            modifier = Modifier.size(size.dp),
            shape = shape,
            color = MaterialTheme.colorScheme.secondaryContainer
        ) {
            Box(contentAlignment = Alignment.Center) {
                Icon(
                    Icons.Default.QueueMusic,
                    contentDescription = null,
                    modifier = Modifier.size((size * 0.5f).dp),
                    tint = MaterialTheme.colorScheme.onSecondaryContainer
                )
            }
        }
    }
}
