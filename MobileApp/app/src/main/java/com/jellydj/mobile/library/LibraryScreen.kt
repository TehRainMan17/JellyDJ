package com.jellydj.mobile.library

import android.util.Log
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Album
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.Category
import androidx.compose.material.icons.filled.DateRange
import androidx.compose.material.icons.filled.Diamond
import androidx.compose.material.icons.filled.LibraryMusic
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Spa
import androidx.compose.material.icons.filled.Speed
import androidx.compose.material.icons.filled.TrendingDown
import androidx.compose.material.icons.filled.TrendingFlat
import androidx.compose.material.icons.filled.TrendingUp
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.activity.compose.BackHandler
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.Saver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import coil.request.ImageRequest
import com.jellydj.mobile.AppContainer
import com.jellydj.mobile.core.model.ArtistDetail
import com.jellydj.mobile.core.model.LibraryAlbum
import com.jellydj.mobile.core.model.LibraryArtist
import com.jellydj.mobile.core.model.LibraryGenre
import com.jellydj.mobile.core.model.LibraryYear
import com.jellydj.mobile.core.model.SmartCollection
import com.jellydj.mobile.core.model.Track
import com.jellydj.mobile.home.TrackListItem
import com.jellydj.mobile.player.PlayerViewModel
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private enum class LibraryView {
    HOME, ARTISTS, ALBUMS, TRACKS, GENRES, YEARS, SMART_COLLECTIONS
}

// Images shown behind each tile in the home grid
private data class TileImages(
    val artistImages: List<String> = emptyList(),
    val albumImages: List<String> = emptyList(),
)

@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
fun LibraryScreen(
    container: AppContainer,
    playerViewModel: PlayerViewModel,
    onArtistClick: (String) -> Unit,
    onAlbumClick: (String, String) -> Unit,
    onYearClick: (Int) -> Unit,
    onSmartCollectionClick: (String) -> Unit,
) {
    val tag = "LibraryScreen"
    val scope = rememberCoroutineScope()
    // rememberSaveable preserves sub-view state when the user navigates forward
    // (e.g. Library→Artists→Artist detail→back) and comes back to this screen.
    val viewSaver = Saver<LibraryView, String>(save = { it.name }, restore = { LibraryView.valueOf(it) })
    var view by rememberSaveable(stateSaver = viewSaver) { mutableStateOf(LibraryView.HOME) }
    var query by rememberSaveable { mutableStateOf("") }
    var loading by remember { mutableStateOf(false) }
    var activeGenre by rememberSaveable { mutableStateOf<String?>(null) }
    var error by remember { mutableStateOf<String?>(null) }

    val artists = remember { mutableStateListOf<LibraryArtist>() }
    val albums = remember { mutableStateListOf<LibraryAlbum>() }
    val tracks = remember { mutableStateListOf<Track>() }
    val genres = remember { mutableStateListOf<LibraryGenre>() }
    val years = remember { mutableStateListOf<LibraryYear>() }
    val smartCollections = remember { mutableStateListOf<SmartCollection>() }
    var tileImages by remember { mutableStateOf(TileImages()) }

    // Load tile background images once on first composition.
    // Also kick off a catalog version check in the background so subsequent
    // album list loads can serve from the local cache if the version is current.
    LaunchedEffect(Unit) {
        // Catalog check runs concurrently with tile image loading — it's a tiny
        // network call (~200 bytes) and a file write only when version changes.
        launch {
            runCatching { container.libraryRepository.refreshCatalogIfNeeded() }
                .onFailure { Log.w(tag, "Catalog refresh failed silently", it) }
        }
        runCatching {
            coroutineScope {
                val artistsDef = async {
                    container.libraryRepository.libraryArtists(limit = 14)
                        .mapNotNull { it.imageUrl }.filter { it.isNotBlank() }
                }
                val albumsDef = async {
                    container.libraryRepository.libraryAlbums(limit = 14)
                        .mapNotNull { it.imageUrl }.filter { it.isNotBlank() }
                }
                tileImages = TileImages(
                    artistImages = artistsDef.await(),
                    albumImages = albumsDef.await(),
                )
            }
        }.onFailure {
            Log.w(tag, "Could not load tile images", it)
        }
    }

    suspend fun loadCurrentView() {
        if (view == LibraryView.HOME) return
        loading = true
        error = null
        runCatching {
            when (view) {
                LibraryView.ARTISTS -> {
                    artists.clear()
                    artists.addAll(container.libraryRepository.libraryArtists(query = query))
                }
                LibraryView.ALBUMS -> {
                    albums.clear()
                    albums.addAll(container.libraryRepository.libraryAlbums(query = query))
                }
                LibraryView.TRACKS -> {
                    tracks.clear()
                    tracks.addAll(
                        container.libraryRepository.libraryTracks(
                            query = query,
                            genre = activeGenre,
                            sort = "personal"
                        )
                    )
                }
                LibraryView.GENRES -> {
                    genres.clear()
                    genres.addAll(container.libraryRepository.libraryGenres(query = query))
                }
                LibraryView.YEARS -> {
                    years.clear()
                    years.addAll(container.libraryRepository.libraryYears())
                }
                LibraryView.SMART_COLLECTIONS -> {
                    smartCollections.clear()
                    smartCollections.addAll(container.libraryRepository.smartCollections())
                }
                LibraryView.HOME -> Unit
            }
        }.onFailure {
            error = it.message ?: it::class.java.simpleName
            Log.e(tag, "Failed loading view=$view query='$query' genre='$activeGenre'", it)
        }
        loading = false
    }

    LaunchedEffect(view, query, activeGenre) { loadCurrentView() }

    BackHandler(enabled = view != LibraryView.HOME) {
        view = LibraryView.HOME
        activeGenre = null
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        when (view) {
                            LibraryView.HOME -> "Library"
                            LibraryView.ARTISTS -> "Artists"
                            LibraryView.ALBUMS -> "Albums"
                            LibraryView.TRACKS -> if (activeGenre != null) "Tracks • $activeGenre" else "Tracks"
                            LibraryView.GENRES -> "Genres"
                            LibraryView.YEARS -> "Years"
                            LibraryView.SMART_COLLECTIONS -> "Smart Collections"
                        }
                    )
                },
                navigationIcon = {
                    if (view != LibraryView.HOME) {
                        IconButton(onClick = {
                            view = LibraryView.HOME
                            activeGenre = null
                        }) {
                            Icon(Icons.Default.ArrowBack, contentDescription = "Back")
                        }
                    }
                }
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
        ) {
            if (view != LibraryView.YEARS && view != LibraryView.SMART_COLLECTIONS && view != LibraryView.HOME) {
                OutlinedTextField(
                    value = query,
                    onValueChange = { query = it },
                    label = { Text("Search your library...") },
                    leadingIcon = { Icon(Icons.Default.Search, contentDescription = null) },
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 8.dp),
                    singleLine = true
                )
            }

            if (loading) {
                Row(
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                    Text("Loading...")
                }
            }
            if (!error.isNullOrBlank()) {
                Row(
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 6.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = "Error: $error",
                        color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.weight(1f)
                    )
                    TextButton(onClick = { scope.launch { loadCurrentView() } }) {
                        Text("Retry")
                    }
                }
            }

            when (view) {
                LibraryView.HOME -> {
                    BrowseHomeGrid(
                        tileImages = tileImages,
                        onArtists = { view = LibraryView.ARTISTS },
                        onAlbums = { view = LibraryView.ALBUMS },
                        onTracks = { view = LibraryView.TRACKS },
                        onGenres = { view = LibraryView.GENRES },
                        onYears = { view = LibraryView.YEARS },
                        onSmart = { view = LibraryView.SMART_COLLECTIONS }
                    )
                }

                LibraryView.ARTISTS -> {
                    if (!loading && artists.isEmpty()) {
                        EmptyMessage("No artists found.")
                    } else {
                        LazyVerticalGrid(
                            columns = GridCells.Fixed(2),
                            contentPadding = PaddingValues(12.dp),
                            horizontalArrangement = Arrangement.spacedBy(12.dp),
                            verticalArrangement = Arrangement.spacedBy(12.dp),
                            modifier = Modifier.fillMaxSize()
                        ) {
                            items(artists) { artist ->
                                VisualLibraryCard(
                                    title = artist.name,
                                    subtitle = "Affinity ${artist.affinityScore.toInt()} • Global ${artist.globalPopularity?.toInt() ?: 0}",
                                    imageUrl = artist.imageUrl,
                                    onClick = { onArtistClick(artist.name) },
                                    icon = { Icon(Icons.Default.Person, contentDescription = null) }
                                )
                            }
                        }
                    }
                }

                LibraryView.ALBUMS -> {
                    if (!loading && albums.isEmpty()) {
                        EmptyMessage("No albums found.")
                    } else {
                        LazyVerticalGrid(
                            columns = GridCells.Fixed(2),
                            contentPadding = PaddingValues(12.dp),
                            horizontalArrangement = Arrangement.spacedBy(12.dp),
                            verticalArrangement = Arrangement.spacedBy(12.dp),
                            modifier = Modifier.fillMaxSize()
                        ) {
                            items(albums) { album ->
                                VisualLibraryCard(
                                    title = album.name,
                                    subtitle = "${album.artist} • Affinity ${album.affinityScore.toInt()}",
                                    imageUrl = album.imageUrl,
                                    onClick = { onAlbumClick(album.artist, album.name) },
                                    icon = { Icon(Icons.Default.Album, contentDescription = null) }
                                )
                            }
                        }
                    }
                }

                LibraryView.TRACKS -> {
                    if (activeGenre != null) {
                        Row(modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp)) {
                            FilterChip(
                                selected = true,
                                onClick = { activeGenre = null },
                                label = { Text("Genre: $activeGenre (tap to clear)") }
                            )
                        }
                    }
                    if (!loading && tracks.isEmpty()) {
                        EmptyMessage("No tracks found.")
                    } else {
                        LazyColumn(contentPadding = PaddingValues(bottom = 16.dp)) {
                            itemsIndexed(tracks) { index, track ->
                                Column {
                                    TrackListItem(
                                        track = track,
                                        onPlay = {
                                            scope.launch { playerViewModel.playQueue(tracks, index) }
                                        }
                                    )
                                    Text(
                                        "Affinity ${track.artistAffinity?.toInt() ?: 0} • Global ${track.globalPopularity?.toInt() ?: 0} • Plays ${track.playCount}",
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        modifier = Modifier.padding(start = 16.dp, end = 16.dp, bottom = 8.dp)
                                    )
                                }
                            }
                        }
                    }
                }

                LibraryView.GENRES -> {
                    if (!loading && genres.isEmpty()) {
                        EmptyMessage("No genres found.")
                    } else {
                        LazyColumn(contentPadding = PaddingValues(bottom = 16.dp)) {
                            items(genres) { genre ->
                                ListItem(
                                    headlineContent = { Text(genre.name) },
                                    supportingContent = {
                                        Text("Affinity ${genre.affinityScore.toInt()} • ${genre.trackCount} tracks")
                                    },
                                    trailingContent = { Icon(Icons.Default.TrendingUp, null) },
                                    modifier = Modifier.clickable {
                                        activeGenre = genre.name
                                        view = LibraryView.TRACKS
                                    }
                                )
                            }
                        }
                    }
                }

                LibraryView.YEARS -> {
                    if (!loading && years.isEmpty()) {
                        EmptyMessage("No years found.")
                    } else {
                        LazyVerticalGrid(
                            columns = GridCells.Fixed(2),
                            contentPadding = PaddingValues(12.dp),
                            horizontalArrangement = Arrangement.spacedBy(12.dp),
                            verticalArrangement = Arrangement.spacedBy(12.dp),
                            modifier = Modifier.fillMaxSize()
                        ) {
                            items(years) { year ->
                                BrowseTile(
                                    label = year.year.toString(),
                                    sublabel = "${year.trackCount} tracks",
                                    icon = Icons.Default.DateRange,
                                    onClick = { onYearClick(year.year) }
                                )
                            }
                        }
                    }
                }

                LibraryView.SMART_COLLECTIONS -> {
                    if (!loading && smartCollections.isEmpty()) {
                        EmptyMessage("No smart collections available.")
                    } else {
                        LazyVerticalGrid(
                            columns = GridCells.Fixed(2),
                            contentPadding = PaddingValues(12.dp),
                            horizontalArrangement = Arrangement.spacedBy(12.dp),
                            verticalArrangement = Arrangement.spacedBy(12.dp),
                            modifier = Modifier.fillMaxSize()
                        ) {
                            items(smartCollections) { collection ->
                                BrowseTile(
                                    label = collection.label,
                                    sublabel = collection.description,
                                    icon = smartCollectionIcon(collection.iconHint),
                                    onClick = { onSmartCollectionClick(collection.key) }
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

// Tile layout: index → (images pool, startImageIndex, startDelayMs)
// artistImages covers [0,1]: Artists, Genres
// albumImages covers [2,3,4,5]: Albums, Tracks, Years, Smart
// Each tile gets a staggered start so they never all crossfade at once.
private data class TileConfig(
    val images: List<String>,
    val startIndex: Int,
    val startDelayMs: Long,
)

private fun tileConfigs(t: TileImages): List<TileConfig> {
    val a = t.artistImages
    val b = t.albumImages
    val aSize = a.size.coerceAtLeast(1)
    val bSize = b.size.coerceAtLeast(1)
    return listOf(
        TileConfig(a, 0, 0L),                          // Artists
        TileConfig(b, 0, 1_400L),                      // Albums
        TileConfig(b, bSize / 2, 2_800L),              // Tracks
        TileConfig(a, aSize / 2, 4_200L),              // Genres
        TileConfig(b, (bSize * 3 / 4) % bSize, 5_600L), // Years
        TileConfig(a, (aSize * 3 / 4) % aSize, 7_000L), // Smart
    )
}

@Composable
private fun BrowseHomeGrid(
    tileImages: TileImages,
    onArtists: () -> Unit,
    onAlbums: () -> Unit,
    onTracks: () -> Unit,
    onGenres: () -> Unit,
    onYears: () -> Unit,
    onSmart: () -> Unit,
) {
    val configs = remember(tileImages) { tileConfigs(tileImages) }

    data class TileDef(val label: String, val icon: ImageVector, val onClick: () -> Unit, val configIndex: Int)

    val tiles = listOf(
        TileDef("Artists", Icons.Default.Person, onArtists, 0),
        TileDef("Albums", Icons.Default.Album, onAlbums, 1),
        TileDef("Tracks", Icons.Default.LibraryMusic, onTracks, 2),
        TileDef("Genres", Icons.Default.Category, onGenres, 3),
        TileDef("Years", Icons.Default.DateRange, onYears, 4),
        TileDef("Smart", Icons.Default.AutoAwesome, onSmart, 5),
    )

    LazyVerticalGrid(
        columns = GridCells.Fixed(2),
        contentPadding = PaddingValues(12.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
        modifier = Modifier.fillMaxSize()
    ) {
        items(tiles, key = { it.label }) { tile ->
            val cfg = configs[tile.configIndex]
            BrowseTile(
                label = tile.label,
                icon = tile.icon,
                onClick = tile.onClick,
                images = cfg.images,
                startImageIndex = cfg.startIndex,
                startDelayMs = cfg.startDelayMs,
            )
        }
    }
}

@Composable
private fun BrowseTile(
    label: String,
    sublabel: String? = null,
    icon: ImageVector,
    onClick: () -> Unit,
    images: List<String> = emptyList(),
    startImageIndex: Int = 0,
    startDelayMs: Long = 0L,
) {
    val hasImages = images.isNotEmpty()
    var currentIndex by remember(images, startImageIndex) {
        mutableIntStateOf(if (hasImages) startImageIndex % images.size else 0)
    }

    LaunchedEffect(images, startDelayMs) {
        if (images.size <= 1) return@LaunchedEffect
        delay(startDelayMs)
        while (true) {
            delay(7_000L)
            currentIndex = (currentIndex + 1) % images.size
        }
    }

    Card(
        onClick = onClick,
        modifier = Modifier
            .fillMaxWidth()
            .aspectRatio(1f),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)
    ) {
        Box(modifier = Modifier.fillMaxSize()) {

            // Rotating background image with crossfade
            if (hasImages) {
                AnimatedContent(
                    targetState = images[currentIndex],
                    transitionSpec = {
                        fadeIn(tween(durationMillis = 1_200)) togetherWith
                            fadeOut(tween(durationMillis = 1_200))
                    },
                    label = "tile_bg_$label"
                ) { url ->
                    AsyncImage(
                        model = ImageRequest.Builder(LocalContext.current)
                            .data(url)
                            .crossfade(true)
                            .build(),
                        contentDescription = null,
                        contentScale = ContentScale.Crop,
                        modifier = Modifier.fillMaxSize()
                    )
                }
            } else {
                // Gradient fallback while images are loading or unavailable
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .background(
                            Brush.linearGradient(
                                listOf(
                                    MaterialTheme.colorScheme.primary.copy(alpha = 0.28f),
                                    MaterialTheme.colorScheme.secondary.copy(alpha = 0.18f)
                                )
                            )
                        )
                )
            }

            // Dark scrim so text is readable over any photo
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(
                        if (hasImages)
                            Brush.verticalGradient(
                                listOf(
                                    Color.Black.copy(alpha = 0.20f),
                                    Color.Black.copy(alpha = 0.62f)
                                )
                            )
                        else
                            Brush.linearGradient(listOf(Color.Transparent, Color.Transparent))
                    )
            )

            // Icon + label
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(14.dp),
                verticalArrangement = Arrangement.SpaceBetween
            ) {
                Icon(
                    imageVector = icon,
                    contentDescription = null,
                    modifier = Modifier.size(34.dp),
                    tint = if (hasImages) Color.White else MaterialTheme.colorScheme.onSurfaceVariant
                )
                Column {
                    Text(
                        label,
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.Bold,
                        color = if (hasImages) Color.White else MaterialTheme.colorScheme.onSurface
                    )
                    if (sublabel != null) {
                        Text(
                            sublabel,
                            style = MaterialTheme.typography.bodySmall,
                            color = if (hasImages) Color.White.copy(alpha = 0.80f)
                                    else MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun VisualLibraryCard(
    title: String,
    subtitle: String,
    imageUrl: String?,
    icon: @Composable () -> Unit,
    onClick: () -> Unit
) {
    Card(
        onClick = onClick,
        modifier = Modifier.fillMaxWidth()
    ) {
        Column {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .aspectRatio(1f)
                    .background(MaterialTheme.colorScheme.surfaceVariant),
                contentAlignment = Alignment.Center
            ) {
                if (!imageUrl.isNullOrBlank()) {
                    AsyncImage(
                        model = ImageRequest.Builder(LocalContext.current)
                            .data(imageUrl)
                            .crossfade(true)
                            .build(),
                        contentDescription = null,
                        contentScale = ContentScale.Crop,
                        modifier = Modifier.fillMaxSize()
                    )
                } else {
                    icon()
                }
            }
            Column(modifier = Modifier.padding(10.dp)) {
                Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.height(2.dp))
                Text(
                    subtitle,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
    }
}

@Composable
private fun EmptyMessage(text: String) {
    Box(
        modifier = Modifier.fillMaxSize(),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text,
            style = MaterialTheme.typography.bodyLarge,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
    }
}

private fun smartCollectionIcon(hint: String): ImageVector = when (hint) {
    "play_arrow" -> Icons.Default.PlayArrow
    "diamond" -> Icons.Default.Diamond
    "bolt" -> Icons.Default.Bolt
    "spa" -> Icons.Default.Spa
    "speed" -> Icons.Default.Speed
    "trending_up" -> Icons.Default.TrendingUp
    else -> Icons.Default.AutoAwesome
}

private val SORT_OPTIONS = listOf(
    "personal" to "Personal",
    "global" to "Global",
    "plays" to "Plays",
    "bpm" to "BPM",
    "energy" to "Energy"
)

@Composable
private fun SortChipRow(
    currentSort: String,
    options: List<Pair<String, String>> = SORT_OPTIONS,
    onSortChange: (String) -> Unit
) {
    Row(
        modifier = Modifier
            .horizontalScroll(rememberScrollState())
            .padding(horizontal = 16.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        options.forEach { (value, label) ->
            FilterChip(
                selected = currentSort == value,
                onClick = { onSortChange(value) },
                label = { Text(label) }
            )
        }
    }
}

@Composable
private fun TrackSubtitle(track: Track, sort: String) {
    val text = when (sort) {
        "global" -> "Global ${track.globalPopularity?.toInt() ?: 0} • Plays ${track.playCount}"
        "plays" -> "Plays ${track.playCount} • Affinity ${track.artistAffinity?.toInt() ?: 0}"
        "bpm" -> buildString {
            if (track.bpm != null) append("${track.bpm} BPM • ")
            append("Plays ${track.playCount}")
        }
        "energy" -> buildString {
            if (track.energy != null) append("Energy ${(track.energy * 100).toInt()}% • ")
            append("Plays ${track.playCount}")
        }
        else -> "Affinity ${track.artistAffinity?.toInt() ?: 0} • Global ${track.globalPopularity?.toInt() ?: 0} • Plays ${track.playCount}"
    }
    Text(
        text,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(start = 16.dp, end = 16.dp, bottom = 8.dp)
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ArtistLibraryDetailScreen(
    container: AppContainer,
    playerViewModel: PlayerViewModel,
    artistName: String,
    onBack: () -> Unit,
    onRelatedArtistClick: (String) -> Unit = {}
) {
    val tag = "ArtistLibraryDetail"
    val scope = rememberCoroutineScope()
    var sort by remember { mutableStateOf("personal") }
    var query by remember { mutableStateOf("") }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var detail by remember { mutableStateOf<ArtistDetail?>(null) }
    val tracks = remember { mutableStateListOf<Track>() }

    LaunchedEffect(artistName) {
        runCatching {
            detail = container.libraryRepository.artistDetail(artistName)
        }.onFailure {
            Log.w(tag, "Artist detail unavailable for '$artistName'", it)
        }
    }

    LaunchedEffect(artistName, sort, query) {
        loading = true
        error = null
        tracks.clear()
        runCatching {
            tracks.addAll(container.libraryRepository.artistTracks(artistName, sort = sort, query = query))
        }.onFailure {
            error = it.message ?: it::class.java.simpleName
            Log.e(tag, "Failed loading artist tracks for '$artistName'", it)
        }
        loading = false
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(artistName, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                navigationIcon = {
                    IconButton(onClick = onBack) { Icon(Icons.Default.ArrowBack, contentDescription = "Back") }
                }
            )
        }
    ) { padding ->
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
            contentPadding = PaddingValues(bottom = 16.dp)
        ) {
            item { ArtistHeroSection(detail = detail, artistName = artistName) }

            val related = detail?.relatedArtists.orEmpty()
            if (related.isNotEmpty()) {
                item {
                    Text(
                        "Related Artists",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(start = 16.dp, top = 12.dp, bottom = 4.dp)
                    )
                    LazyRow(
                        contentPadding = PaddingValues(horizontal = 16.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        items(related) { artist ->
                            SuggestionChip(
                                onClick = { onRelatedArtistClick(artist.name) },
                                label = { Text(artist.name) }
                            )
                        }
                    }
                }
            }

            item {
                OutlinedTextField(
                    value = query,
                    onValueChange = { query = it },
                    label = { Text("Filter tracks...") },
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 8.dp),
                    singleLine = true
                )
            }
            item {
                SortChipRow(currentSort = sort, onSortChange = { sort = it })
                Spacer(Modifier.height(4.dp))
            }

            if (loading) {
                item {
                    Row(
                        modifier = Modifier.padding(16.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                        Text("Loading...")
                    }
                }
            }
            if (!error.isNullOrBlank()) {
                item {
                    Text(
                        "Error: $error",
                        color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp)
                    )
                }
            }

            itemsIndexed(tracks) { index, track ->
                Column {
                    TrackListItem(
                        track = track,
                        onPlay = { scope.launch { playerViewModel.playQueue(tracks, index) } }
                    )
                    TrackSubtitle(track = track, sort = sort)
                }
            }
        }
    }
}

@Composable
private fun ArtistHeroSection(detail: ArtistDetail?, artistName: String) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height(220.dp)
            .background(MaterialTheme.colorScheme.surfaceVariant)
    ) {
        val imageUrl = detail?.imageUrl
        if (!imageUrl.isNullOrBlank()) {
            AsyncImage(
                model = ImageRequest.Builder(LocalContext.current)
                    .data(imageUrl)
                    .crossfade(true)
                    .build(),
                contentDescription = null,
                contentScale = ContentScale.Crop,
                modifier = Modifier.fillMaxSize()
            )
        }
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(
                    Brush.verticalGradient(
                        listOf(Color.Transparent, Color.Black.copy(alpha = 0.75f))
                    )
                )
        )
        Column(
            modifier = Modifier
                .align(Alignment.BottomStart)
                .padding(16.dp)
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                Text(
                    artistName,
                    style = MaterialTheme.typography.headlineSmall,
                    fontWeight = FontWeight.Bold,
                    color = Color.White,
                    modifier = Modifier.weight(1f, fill = false),
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
                val trend = detail?.trendDirection
                if (trend != null) TrendBadge(trend)
            }
            if (detail != null) {
                val statsText = buildString {
                    append("Affinity ${detail.affinityScore.toInt()}")
                    if (detail.globalPopularity != null) append(" • Global ${detail.globalPopularity.toInt()}")
                }
                Text(statsText, style = MaterialTheme.typography.bodySmall, color = Color.White.copy(alpha = 0.85f))
                val genres = detail.canonicalGenres.take(3)
                if (genres.isNotEmpty()) {
                    Spacer(Modifier.height(4.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        genres.forEach { g ->
                            AssistChip(
                                onClick = {},
                                label = { Text(g.genre, style = MaterialTheme.typography.labelSmall) },
                                colors = AssistChipDefaults.assistChipColors(
                                    containerColor = Color.White.copy(alpha = 0.15f),
                                    labelColor = Color.White
                                ),
                                border = null
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun TrendBadge(trend: String) {
    val (icon, color) = when (trend) {
        "rising" -> Icons.Default.TrendingUp to Color(0xFF4CAF50)
        "falling" -> Icons.Default.TrendingDown to Color(0xFFF44336)
        else -> Icons.Default.TrendingFlat to Color.White.copy(alpha = 0.6f)
    }
    Icon(icon, contentDescription = trend, tint = color, modifier = Modifier.size(20.dp))
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AlbumLibraryDetailScreen(
    container: AppContainer,
    playerViewModel: PlayerViewModel,
    artistName: String,
    albumName: String,
    onBack: () -> Unit
) {
    val tag = "AlbumLibraryDetail"
    val scope = rememberCoroutineScope()
    var sort by remember { mutableStateOf("personal") }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    val tracks = remember { mutableStateListOf<Track>() }

    LaunchedEffect(artistName, albumName, sort) {
        loading = true
        error = null
        tracks.clear()
        runCatching {
            tracks.addAll(
                container.libraryRepository.libraryTracks(
                    artist = if (artistName == "Various Artists") null else artistName,
                    album = albumName,
                    sort = sort
                )
            )
        }.onFailure {
            error = it.message ?: it::class.java.simpleName
            Log.e(tag, "Failed loading album tracks for '$artistName' / '$albumName'", it)
        }
        loading = false
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(albumName, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                navigationIcon = {
                    IconButton(onClick = onBack) { Icon(Icons.Default.ArrowBack, contentDescription = "Back") }
                }
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
        ) {
            Text(
                artistName,
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp)
            )
            SortChipRow(
                currentSort = sort,
                options = listOf("personal" to "Personal", "global" to "Global", "plays" to "Plays"),
                onSortChange = { sort = it }
            )
            Spacer(Modifier.height(8.dp))
            if (loading) CircularProgressIndicator(modifier = Modifier.padding(16.dp))
            if (!error.isNullOrBlank()) {
                Text(
                    text = "Error: $error",
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp)
                )
            }
            LazyColumn(contentPadding = PaddingValues(bottom = 16.dp)) {
                itemsIndexed(tracks) { index, track ->
                    Column {
                        TrackListItem(
                            track = track,
                            onPlay = { scope.launch { playerViewModel.playQueue(tracks, index) } }
                        )
                        TrackSubtitle(track = track, sort = sort)
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun YearLibraryDetailScreen(
    container: AppContainer,
    playerViewModel: PlayerViewModel,
    year: Int,
    onBack: () -> Unit
) {
    val tag = "YearLibraryDetail"
    val scope = rememberCoroutineScope()
    var sort by remember { mutableStateOf("personal") }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    val tracks = remember { mutableStateListOf<Track>() }

    LaunchedEffect(year, sort) {
        loading = true; error = null; tracks.clear()
        runCatching {
            tracks.addAll(container.libraryRepository.yearTracks(year, sort))
        }.onFailure {
            error = it.message ?: it::class.java.simpleName
            Log.e(tag, "Failed loading tracks for year $year", it)
        }
        loading = false
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(year.toString()) },
                navigationIcon = {
                    IconButton(onClick = onBack) { Icon(Icons.Default.ArrowBack, contentDescription = "Back") }
                }
            )
        }
    ) { padding ->
        Column(modifier = Modifier.fillMaxSize().padding(padding)) {
            SortChipRow(
                currentSort = sort,
                options = listOf("personal" to "Personal", "global" to "Global", "plays" to "Plays"),
                onSortChange = { sort = it }
            )
            Spacer(Modifier.height(4.dp))
            if (loading) {
                Row(modifier = Modifier.padding(16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                    Text("Loading...")
                }
            }
            if (!error.isNullOrBlank()) {
                Text("Error: $error", color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp))
            }
            if (!loading && tracks.isEmpty()) {
                EmptyMessage("No tracks found for $year.")
            } else {
                LazyColumn(contentPadding = PaddingValues(bottom = 16.dp)) {
                    itemsIndexed(tracks) { index, track ->
                        Column {
                            TrackListItem(track = track, onPlay = { scope.launch { playerViewModel.playQueue(tracks, index) } })
                            TrackSubtitle(track = track, sort = sort)
                        }
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SmartCollectionDetailScreen(
    container: AppContainer,
    playerViewModel: PlayerViewModel,
    collectionKey: String,
    onBack: () -> Unit
) {
    val tag = "SmartCollectionDetail"
    val scope = rememberCoroutineScope()
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    val tracks = remember { mutableStateListOf<Track>() }

    val label = smartCollectionLabel(collectionKey)
    val description = smartCollectionDescription(collectionKey)

    LaunchedEffect(collectionKey) {
        loading = true; error = null; tracks.clear()
        runCatching {
            tracks.addAll(container.libraryRepository.smartCollectionTracks(collectionKey))
        }.onFailure {
            error = it.message ?: it::class.java.simpleName
            Log.e(tag, "Failed loading smart collection '$collectionKey'", it)
        }
        loading = false
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(label) },
                navigationIcon = {
                    IconButton(onClick = onBack) { Icon(Icons.Default.ArrowBack, contentDescription = "Back") }
                }
            )
        }
    ) { padding ->
        Column(modifier = Modifier.fillMaxSize().padding(padding)) {
            Row(
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Icon(
                    smartCollectionIcon(smartCollectionIconHint(collectionKey)),
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.size(32.dp)
                )
                Column {
                    Text(label, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                    Text(description, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            if (loading) {
                Row(modifier = Modifier.padding(16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                    Text("Loading...")
                }
            }
            if (!error.isNullOrBlank()) {
                Text("Error: $error", color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp))
            }
            if (!loading && tracks.isEmpty()) {
                EmptyMessage("No tracks available for this collection.")
            } else {
                val sortHint = smartCollectionSortHint(collectionKey)
                LazyColumn(contentPadding = PaddingValues(bottom = 16.dp)) {
                    itemsIndexed(tracks) { index, track ->
                        Column {
                            TrackListItem(track = track, onPlay = { scope.launch { playerViewModel.playQueue(tracks, index) } })
                            TrackSubtitle(track = track, sort = sortHint)
                        }
                    }
                }
            }
        }
    }
}

private fun smartCollectionLabel(key: String): String = when (key) {
    "top_played" -> "Top Played"
    "hidden_gems" -> "Hidden Gems"
    "high_energy" -> "High Energy"
    "acoustic_chill" -> "Acoustic Chill"
    "fast_tempo" -> "Fast Tempo"
    "rising_artists" -> "Rising Artists"
    else -> key.replace('_', ' ').replaceFirstChar { it.uppercase() }
}

private fun smartCollectionDescription(key: String): String = when (key) {
    "top_played" -> "Your most-played tracks of all time"
    "hidden_gems" -> "High affinity, low global popularity"
    "high_energy" -> "Maximum energy tracks"
    "acoustic_chill" -> "Warm, acoustic, mellow tracks"
    "fast_tempo" -> "Tracks with the highest BPM"
    "rising_artists" -> "Tracks by globally trending artists"
    else -> ""
}

private fun smartCollectionIconHint(key: String): String = when (key) {
    "top_played" -> "play_arrow"
    "hidden_gems" -> "diamond"
    "high_energy" -> "bolt"
    "acoustic_chill" -> "spa"
    "fast_tempo" -> "speed"
    "rising_artists" -> "trending_up"
    else -> "auto_awesome"
}

private fun smartCollectionSortHint(key: String): String = when (key) {
    "top_played", "rising_artists" -> "plays"
    "high_energy" -> "energy"
    "fast_tempo" -> "bpm"
    else -> "personal"
}
