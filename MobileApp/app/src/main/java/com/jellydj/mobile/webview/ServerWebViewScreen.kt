package com.jellydj.mobile.webview

import android.annotation.SuppressLint
import android.net.Uri
import android.webkit.JavascriptInterface
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView

// Token bridge exposed to the bootstrap page so the refresh token is never
// interpolated into a JavaScript string literal (avoids injection via
// insufficient escaping of unicode line terminators, </script> etc.).
private class TokenBridge(private val token: String) {
    @JavascriptInterface
    fun provide(): String = token
}

@OptIn(ExperimentalMaterial3Api::class)
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun ServerWebViewScreen(
    title: String,
    url: String,
    refreshToken: String,
    onBack: () -> Unit
) {
    var loading by remember { mutableStateOf(true) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(title) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                }
            )
        }
    ) { padding ->
        Box(modifier = Modifier.fillMaxSize().padding(padding)) {
            AndroidView(
                factory = { ctx ->
                    val parsed = Uri.parse(url)
                    val origin = "${parsed.scheme}://${parsed.authority}"

                    WebView(ctx).apply {
                        settings.javaScriptEnabled = true
                        settings.domStorageEnabled = true
                        settings.useWideViewPort = false
                        settings.loadWithOverviewMode = false
                        settings.builtInZoomControls = false
                        settings.displayZoomControls = false
                        settings.cacheMode = WebSettings.LOAD_DEFAULT
                        // Never load mixed HTTP content inside an HTTPS page.
                        settings.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW

                        // Expose the refresh token via a JavascriptInterface so it is
                        // never interpolated into a string literal in the page source.
                        addJavascriptInterface(TokenBridge(refreshToken), "__jellydjTokenBridge")

                        var bootstrapDone = false
                        webViewClient = object : WebViewClient() {
                            override fun onPageFinished(view: WebView, pageUrl: String) {
                                if (!bootstrapDone) {
                                    bootstrapDone = true
                                    view.loadUrl(url)
                                    return
                                }

                                loading = false

                                // Inject token into storage using the bridge — no string
                                // interpolation of the token value into JS source.
                                view.evaluateJavascript(
                                    "(function(){" +
                                        "var b=window.__jellydjTokenBridge;" +
                                        "if(b){" +
                                            "var t=b.provide();" +
                                            "try{localStorage.setItem('jellydj_refresh_token',t);}catch(e){}" +
                                            "try{sessionStorage.setItem('jellydj_refresh_token',t);}catch(e){}" +
                                        "}" +
                                    "})()",
                                    null
                                )

                                // CSP-safe fix for WebView height collapse:
                                // inject a .h-screen override through CSSOM rules,
                                // not inline styles.
                                view.evaluateJavascript(
                                    "(function(){" +
                                        "var h=Math.max(window.innerHeight||0,document.documentElement.clientHeight||0,screen.height||0);" +
                                        "if(h>0&&!window.__jellydjCssRuleFix){" +
                                            "window.__jellydjCssRuleFix=true;" +
                                            "var ruleHeight='.h-screen{height:'+h+'px!important;min-height:'+h+'px!important;}';" +
                                            "var ruleHideNestedMenu='header button.lg\\\\:hidden{display:none!important;}';" +
                                            "for(var i=0;i<document.styleSheets.length;i++){" +
                                                "var ss=document.styleSheets[i];" +
                                                "try{" +
                                                    "var idx=ss.cssRules?ss.cssRules.length:0;" +
                                                    "ss.insertRule(ruleHeight, idx);" +
                                                    "idx=ss.cssRules?ss.cssRules.length:0;" +
                                                    "ss.insertRule(ruleHideNestedMenu, idx);" +
                                                    "break;" +
                                                "}catch(e){}" +
                                            "}" +
                                        "}" +
                                    "})()",
                                    null
                                )
                            }
                        }

                        // Bootstrap page: sets localStorage via the bridge before navigating
                        // to the real URL. No token data is embedded in the HTML source.
                        loadDataWithBaseURL(
                            "$origin/",
                            "<!DOCTYPE html><html><body><script>" +
                                "(function(){var b=window.__jellydjTokenBridge;" +
                                "if(b){var t=b.provide();" +
                                "try{localStorage.setItem('jellydj_refresh_token',t);}catch(e){}" +
                                "try{sessionStorage.setItem('jellydj_refresh_token',t);}catch(e){}}" +
                                "})();" +
                                "</script></body></html>",
                            "text/html",
                            "UTF-8",
                            null
                        )
                    }
                },
                modifier = Modifier.fillMaxSize()
            )

            if (loading) {
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .background(MaterialTheme.colorScheme.background),
                    contentAlignment = Alignment.Center
                ) {
                    CircularProgressIndicator()
                }
            }
        }
    }
}
