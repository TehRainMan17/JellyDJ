package com.jellydj.mobile.navigation

import android.net.Uri
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AdminPanelSettings
import androidx.compose.material.icons.filled.Backup
import androidx.compose.material.icons.filled.BarChart
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.QueueMusic
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.Surface
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationDrawerItem
import androidx.compose.material3.NavigationDrawerItemDefaults
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.jellydj.mobile.AppContainer
import com.jellydj.mobile.R
import com.jellydj.mobile.auth.LoginInput
import com.jellydj.mobile.home.HomeScreen
import com.jellydj.mobile.player.MiniPlayer
import com.jellydj.mobile.player.NowPlayingScreen
import com.jellydj.mobile.player.PlayerViewModel
import com.jellydj.mobile.playlists.PlaylistDetailScreen
import com.jellydj.mobile.playlists.PlaylistsScreen
import com.jellydj.mobile.search.SearchScreen
import com.jellydj.mobile.settings.SettingsScreen
import com.jellydj.mobile.webview.ServerWebViewScreen
import kotlinx.coroutines.launch
import retrofit2.HttpException
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import javax.net.ssl.SSLException

@Composable
fun JellyDjMobileApp(container: AppContainer, playerViewModel: PlayerViewModel) {
    var authState by remember { mutableStateOf(AuthState.CHECKING) }

    LaunchedEffect(Unit) {
        if (container.sessionStore.read() == null) {
            authState = AuthState.LOGGED_OUT
            return@LaunchedEffect
        }
        val ok = container.authRepository.currentUser().isSuccess
            || (container.authRepository.refreshSession() && container.authRepository.currentUser().isSuccess)
        if (ok) {
            authState = AuthState.LOGGED_IN
        } else {
            container.authRepository.logout()
            authState = AuthState.LOGGED_OUT
        }
    }

    Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
    when (authState) {
        AuthState.CHECKING -> {
            Column(
                modifier = Modifier.fillMaxSize().padding(20.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Text("Reconnecting to JellyDJ...")
                CircularProgressIndicator(modifier = Modifier.size(24.dp))
            }
        }
        AuthState.LOGGED_OUT -> {
            LoginScreen(
                initialServerUrl = container.sessionStore.readServerBaseUrl().orEmpty(),
                onVerifyServer = { url -> container.authRepository.verifyInstance(url) },
                onLogin = { input ->
                    val ok = container.authRepository.login(input).isSuccess
                    if (ok) authState = AuthState.LOGGED_IN
                    ok
                }
            )
        }
        AuthState.LOGGED_IN -> {
            MainScreen(
                container = container,
                playerViewModel = playerViewModel,
                onSessionInvalid = {
                    container.authRepository.logout()
                    authState = AuthState.LOGGED_OUT
                }
            )
        }
    }
    }
}

@Composable
private fun MainScreen(
    container: AppContainer,
    playerViewModel: PlayerViewModel,
    onSessionInvalid: () -> Unit
) {
    val navController = rememberNavController()
    val navBackStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = navBackStackEntry?.destination?.route
    val drawerState = rememberDrawerState(initialValue = DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    val session = container.sessionStore.read()
    val serverBaseUrl = container.sessionStore.readServerBaseUrl().orEmpty().trimEnd('/')
    val refreshToken = session?.refreshToken.orEmpty()
    val isAdmin = session?.isAdmin ?: false
    val playerState by playerViewModel.uiState.collectAsState()

    data class BottomNavItem(val route: String, val label: String, val icon: ImageVector)
    data class DrawerFeature(val label: String, val icon: ImageVector, val path: String, val adminOnly: Boolean = false)

    val bottomNavItems = listOf(
        BottomNavItem("home", "Home", Icons.Default.Home),
        BottomNavItem("search", "Search", Icons.Default.Search),
        BottomNavItem("playlists", "Playlists", Icons.Default.QueueMusic),
    )

    val drawerFeatures = listOf(
        DrawerFeature("Insights", Icons.Default.BarChart, "insights"),
        DrawerFeature("Playlist Import", Icons.Default.Download, "import"),
        DrawerFeature("Playlist Backup", Icons.Default.Backup, "admin/playlist-backups", adminOnly = true),
        DrawerFeature("Admin Controls", Icons.Default.AdminPanelSettings, "admin/users", adminOnly = true),
    )

    val showBottomNav = currentRoute in bottomNavItems.map { it.route }
    val showMiniPlayer = playerState.hasMedia && currentRoute != "now_playing"

    ModalNavigationDrawer(
        drawerState = drawerState,
        drawerContent = {
            ModalDrawerSheet {
                Spacer(Modifier.height(16.dp))
                Row(
                    modifier = Modifier.padding(horizontal = 20.dp, vertical = 8.dp),
                    horizontalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    Image(
                        painter = painterResource(R.drawable.jellydj_logo),
                        contentDescription = null,
                        modifier = Modifier.size(40.dp)
                    )
                    Column {
                        Text("JellyDJ", style = MaterialTheme.typography.titleMedium)
                        if (session?.username != null) {
                            Text(
                                session.username,
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                }
                HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
                Text(
                    "App",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 24.dp, vertical = 4.dp)
                )
                NavigationDrawerItem(
                    icon = { Icon(Icons.Default.Settings, contentDescription = null) },
                    label = { Text("Audio Settings") },
                    selected = currentRoute == "settings",
                    onClick = {
                        scope.launch { drawerState.close() }
                        navController.navigate("settings")
                    },
                    modifier = Modifier.padding(NavigationDrawerItemDefaults.ItemPadding)
                )
                HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
                Text(
                    "Server Features",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 24.dp, vertical = 4.dp)
                )
                drawerFeatures.forEach { feature ->
                    if (!feature.adminOnly || isAdmin) {
                        NavigationDrawerItem(
                            icon = { Icon(feature.icon, contentDescription = null) },
                            label = { Text(feature.label) },
                            selected = false,
                            onClick = {
                                scope.launch { drawerState.close() }
                                val encodedTitle = Uri.encode(feature.label)
                                val encodedPath = Uri.encode(feature.path)
                                navController.navigate("web_view/$encodedTitle/$encodedPath")
                            },
                            modifier = Modifier.padding(NavigationDrawerItemDefaults.ItemPadding)
                        )
                    }
                }
            }
        }
    ) {
        Scaffold(
            bottomBar = {
                Column {
                    if (showMiniPlayer) {
                        MiniPlayer(
                            playerViewModel = playerViewModel,
                            onTap = { navController.navigate("now_playing") }
                        )
                    }
                    if (showBottomNav) {
                        NavigationBar {
                            bottomNavItems.forEach { item ->
                                NavigationBarItem(
                                    selected = currentRoute == item.route,
                                    onClick = {
                                        navController.navigate(item.route) {
                                            popUpTo("home") { saveState = true }
                                            launchSingleTop = true
                                            restoreState = true
                                        }
                                    },
                                    icon = { Icon(item.icon, contentDescription = item.label) },
                                    label = { Text(item.label) }
                                )
                            }
                        }
                    }
                }
            }
        ) { padding ->
            NavHost(
                navController = navController,
                startDestination = "home",
                modifier = Modifier.padding(padding),
                enterTransition = { fadeIn(tween(220)) },
                exitTransition = { fadeOut(tween(180)) },
                popEnterTransition = { fadeIn(tween(220)) },
                popExitTransition = { fadeOut(tween(180)) }
            ) {
                composable("home") {
                    HomeScreen(
                        container = container,
                        playerViewModel = playerViewModel,
                        onSessionInvalid = onSessionInvalid,
                        onMenuOpen = { scope.launch { drawerState.open() } },
                        onPlaylistClick = { playlistId, playlistName, coverImageUrl ->
                            val encodedName = Uri.encode(playlistName)
                            val encodedCover = Uri.encode(coverImageUrl ?: "")
                            navController.navigate("playlist_detail/$playlistId/$encodedName/$encodedCover")
                        }
                    )
                }
                composable("search") {
                    SearchScreen(container = container, playerViewModel = playerViewModel)
                }
                composable("playlists") {
                    PlaylistsScreen(
                        container = container,
                        playerViewModel = playerViewModel,
                        onPlaylistClick = { playlistId, playlistName, coverImageUrl ->
                            val encodedName = Uri.encode(playlistName)
                            val encodedCover = Uri.encode(coverImageUrl ?: "")
                            navController.navigate("playlist_detail/$playlistId/$encodedName/$encodedCover")
                        }
                    )
                }
                composable(
                    "playlist_detail/{playlistId}/{playlistName}/{coverImageUrl}",
                    enterTransition = { slideInHorizontally(tween(300)) { it } + fadeIn(tween(300)) },
                    exitTransition = { slideOutHorizontally(tween(250)) { -it / 4 } + fadeOut(tween(250)) },
                    popEnterTransition = { slideInHorizontally(tween(300)) { -it / 4 } + fadeIn(tween(300)) },
                    popExitTransition = { slideOutHorizontally(tween(300)) { it } + fadeOut(tween(300)) }
                ) { backStackEntry ->
                    val playlistId = backStackEntry.arguments?.getString("playlistId") ?: return@composable
                    val playlistName = backStackEntry.arguments?.getString("playlistName") ?: "Playlist"
                    val coverImageUrl = backStackEntry.arguments?.getString("coverImageUrl")?.takeIf { it.isNotBlank() }
                    PlaylistDetailScreen(
                        container = container,
                        playerViewModel = playerViewModel,
                        playlistId = playlistId,
                        playlistName = playlistName,
                        coverImageUrl = coverImageUrl,
                        onBack = { navController.navigateUp() }
                    )
                }
                composable(
                    "now_playing",
                    enterTransition = { slideInVertically(tween(350)) { it } + fadeIn(tween(350)) },
                    exitTransition = { slideOutVertically(tween(300)) { it } + fadeOut(tween(300)) },
                    popEnterTransition = { fadeIn(tween(220)) },
                    popExitTransition = { slideOutVertically(tween(300)) { it } + fadeOut(tween(300)) }
                ) {
                    NowPlayingScreen(
                        playerViewModel = playerViewModel,
                        onBack = { navController.navigateUp() }
                    )
                }
                composable(
                    "settings",
                    enterTransition = { slideInHorizontally(tween(300)) { it } + fadeIn(tween(300)) },
                    exitTransition = { fadeOut(tween(200)) },
                    popEnterTransition = { fadeIn(tween(220)) },
                    popExitTransition = { slideOutHorizontally(tween(300)) { it } + fadeOut(tween(300)) }
                ) {
                    SettingsScreen(onBack = { navController.navigateUp() })
                }
                composable("web_view/{title}/{path}") { backStackEntry ->
                    val title = backStackEntry.arguments?.getString("title") ?: "JellyDJ"
                    val path = backStackEntry.arguments?.getString("path") ?: ""
                    ServerWebViewScreen(
                        title = title,
                        url = "$serverBaseUrl/$path",
                        refreshToken = refreshToken,
                        onBack = { navController.navigateUp() }
                    )
                }
            }
        }
    }
}

private enum class AuthState { CHECKING, LOGGED_OUT, LOGGED_IN }
private enum class LoginStage { SERVER, CREDENTIALS }

@Composable
private fun LoginScreen(
    initialServerUrl: String,
    onVerifyServer: suspend (String) -> Result<Unit>,
    onLogin: suspend (LoginInput) -> Boolean
) {
    val scope = rememberCoroutineScope()
    var serverUrl by remember { mutableStateOf(initialServerUrl) }
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var rememberMe by remember { mutableStateOf(false) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var stage by remember { mutableStateOf(LoginStage.SERVER) }

    Column(
        modifier = Modifier.fillMaxSize().padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Image(
            painter = painterResource(id = R.drawable.jellydj_logo),
            contentDescription = "JellyDJ Logo",
            modifier = Modifier.fillMaxWidth().height(220.dp),
            contentScale = ContentScale.Fit
        )

        Text("JellyDJ", style = MaterialTheme.typography.headlineSmall)

        if (stage == LoginStage.SERVER) {
            Text("Enter the URL you use to open JellyDJ in your browser (e.g. http://jellydj.local:7879)")

            OutlinedTextField(
                value = serverUrl,
                onValueChange = { serverUrl = it },
                label = { Text("JellyDJ URL") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true
            )

            Button(
                onClick = {
                    loading = true; error = null
                    scope.launch {
                        val result = onVerifyServer(serverUrl)
                        loading = false
                        if (result.isSuccess) stage = LoginStage.CREDENTIALS
                        else error = verificationErrorMessage(result.exceptionOrNull())
                    }
                },
                modifier = Modifier.fillMaxWidth(),
                enabled = !loading && serverUrl.isNotBlank()
            ) { Text(if (loading) "Verifying..." else "Verify JellyDJ Instance") }
        } else {
            Text("Server verified. Sign in with your Jellyfin credentials.")

            OutlinedTextField(
                value = username,
                onValueChange = { username = it },
                label = { Text("Username") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true
            )
            OutlinedTextField(
                value = password,
                onValueChange = { password = it },
                label = { Text("Password") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
                visualTransformation = PasswordVisualTransformation()
            )

            Row(
                verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(4.dp)
            ) {
                Checkbox(
                    checked = rememberMe,
                    onCheckedChange = { rememberMe = it }
                )
                Text("Stay logged in for 30 days", style = MaterialTheme.typography.bodySmall)
            }

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { stage = LoginStage.SERVER }, enabled = !loading) {
                    Text("Change Server")
                }
                Button(
                    onClick = {
                        loading = true; error = null
                        scope.launch {
                            val ok = onLogin(LoginInput(username, password, rememberMe))
                            loading = false
                            if (!ok) error = "Login failed. Check username/password."
                        }
                    },
                    enabled = !loading && username.isNotBlank() && password.isNotBlank()
                ) { Text(if (loading) "Signing in..." else "Sign in") }
            }
        }

        if (loading) CircularProgressIndicator(modifier = Modifier.size(24.dp))
        if (error != null) Text(error ?: "", color = MaterialTheme.colorScheme.error)
    }
}

private fun verificationErrorMessage(error: Throwable?): String = when (error) {
    is UnknownHostException -> "Could not resolve server hostname. Check URL and DNS."
    is ConnectException -> "Could not connect. Check host, port, and network access."
    is SocketTimeoutException -> "Server timed out. Check connectivity and reverse proxy."
    is SSLException -> "TLS/SSL error. Verify your HTTPS certificate."
    is HttpException -> "Server rejected verification (HTTP ${error.code()})."
    is IllegalArgumentException -> error.message ?: "Invalid server URL."
    else -> "Could not verify this JellyDJ instance. ${error?.message ?: "Check URL and try again."}"
}
