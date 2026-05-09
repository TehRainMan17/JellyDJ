package com.jellydj.mobile.core.debug

import android.content.Context
import android.os.Build
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID

/**
 * Pipes Android Auto lifecycle events to the JellyDJ backend so a drive-test can
 * be reviewed afterward. Designed to never block, never throw, and never lose
 * events: failed sends are appended to a local JSONL buffer that is drained the
 * next time a POST succeeds.
 *
 * The endpoint (POST /api/debug/aa-event) is unauthenticated by design — AA
 * binds before the JWT refresh path runs and we want the events from that
 * window above all else.
 */
object AaTelemetry {
    private const val TAG = "AaTelemetry"
    private const val PATH = "api/debug/aa-event"
    private const val MAX_BUFFER_BYTES = 512 * 1024
    private const val MAX_BATCH_EVENTS = 200

    private val sessionId: String = UUID.randomUUID().toString()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val ioMutex = Mutex()
    private val isoFormat = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US).apply {
        timeZone = TimeZone.getTimeZone("UTC")
    }

    @Volatile private var client: OkHttpClient? = null
    @Volatile private var bufferFile: File? = null

    fun init(context: Context, httpClient: OkHttpClient) {
        client = httpClient
        bufferFile = File(context.filesDir, "aa_events_pending.jsonl")
        log("telemetry_init", mapOf(
            "device_model" to "${Build.MANUFACTURER} ${Build.MODEL}",
            "android_sdk" to Build.VERSION.SDK_INT,
            "session_id" to sessionId,
        ))
    }

    /** Fire-and-forget. Safe to call from any thread. */
    fun log(event: String, fields: Map<String, Any?> = emptyMap()) {
        val record = buildRecord(event, fields)
        scope.launch { send(record) }
    }

    /** Synchronous flush for use in the uncaught-exception handler path. */
    fun logCrashSync(event: String, fields: Map<String, Any?>) {
        try {
            val record = buildRecord(event, fields)
            // Persist to the buffer first — guaranteed durable even if the network call hangs.
            appendToBuffer(record)
            // Best-effort sync POST with a tight timeout. We're inside the JVM crash path
            // so the process is about to die — do NOT spend more than a couple of seconds.
            val c = client ?: return
            val tight = c.newBuilder()
                .connectTimeout(2, java.util.concurrent.TimeUnit.SECONDS)
                .writeTimeout(2, java.util.concurrent.TimeUnit.SECONDS)
                .readTimeout(2, java.util.concurrent.TimeUnit.SECONDS)
                .build()
            val body = JSONArray().put(JSONObject(record)).toString()
                .toRequestBody("application/json".toMediaType())
            val req = Request.Builder().url("https://placeholder.local/$PATH").post(body).build()
            tight.newCall(req).execute().close()
        } catch (_: Throwable) {
            // Crash handler must never throw.
        }
    }

    private fun buildRecord(event: String, fields: Map<String, Any?>): Map<String, Any?> {
        return mapOf(
            "session_id" to sessionId,
            "ts" to isoFormat.format(Date()),
            "event" to event,
            "fields" to fields,
        )
    }

    private suspend fun send(record: Map<String, Any?>) {
        val c = client ?: return
        ioMutex.withLock {
            val pending = readBuffer()
            val batch = ArrayList<Map<String, Any?>>(pending.size + 1).apply {
                addAll(pending)
                add(record)
            }
            // Cap batch — if buffer is huge, send the head and re-buffer the tail.
            val toSend = if (batch.size > MAX_BATCH_EVENTS) batch.subList(0, MAX_BATCH_EVENTS) else batch
            val ok = post(c, toSend)
            if (ok) {
                if (toSend.size < batch.size) {
                    writeBuffer(batch.subList(toSend.size, batch.size))
                } else {
                    clearBuffer()
                }
            } else {
                writeBuffer(batch)
            }
        }
    }

    private fun post(c: OkHttpClient, batch: List<Map<String, Any?>>): Boolean {
        return try {
            val arr = JSONArray()
            for (rec in batch) arr.put(JSONObject(rec))
            val body = arr.toString().toRequestBody("application/json".toMediaType())
            // Hostname is rewritten by the app's baseUrlOverrideInterceptor — placeholder is fine.
            val req = Request.Builder().url("https://placeholder.local/$PATH").post(body).build()
            c.newCall(req).execute().use { resp -> resp.isSuccessful }
        } catch (e: Throwable) {
            Log.w(TAG, "post failed: ${e.message}")
            false
        }
    }

    private fun readBuffer(): List<Map<String, Any?>> {
        val f = bufferFile ?: return emptyList()
        if (!f.exists()) return emptyList()
        return try {
            f.readLines().mapNotNull { line ->
                if (line.isBlank()) null else jsonObjectToMap(JSONObject(line))
            }
        } catch (_: Throwable) {
            emptyList()
        }
    }

    private fun writeBuffer(records: List<Map<String, Any?>>) {
        val f = bufferFile ?: return
        try {
            // Drop oldest records first if we exceed the size cap.
            val sb = StringBuilder()
            for (rec in records) {
                sb.append(JSONObject(rec).toString()).append('\n')
            }
            var bytes = sb.toString().toByteArray(Charsets.UTF_8)
            if (bytes.size > MAX_BUFFER_BYTES) {
                // Trim from the front (oldest events) to fit.
                val trimmed = String(bytes, Charsets.UTF_8)
                val lines = trimmed.lines().filter { it.isNotBlank() }
                val keep = ArrayDeque(lines)
                while (keep.sumOf { it.length + 1 } > MAX_BUFFER_BYTES && keep.isNotEmpty()) {
                    keep.removeFirst()
                }
                bytes = keep.joinToString("\n", postfix = "\n").toByteArray(Charsets.UTF_8)
            }
            f.writeBytes(bytes)
        } catch (_: Throwable) { }
    }

    private fun appendToBuffer(record: Map<String, Any?>) {
        val f = bufferFile ?: return
        try {
            f.appendText(JSONObject(record).toString() + "\n")
        } catch (_: Throwable) { }
    }

    private fun clearBuffer() {
        val f = bufferFile ?: return
        try { if (f.exists()) f.delete() } catch (_: Throwable) { }
    }

    private fun jsonObjectToMap(obj: JSONObject): Map<String, Any?> {
        val out = HashMap<String, Any?>(obj.length())
        val keys = obj.keys()
        while (keys.hasNext()) {
            val k = keys.next()
            val v = obj.get(k)
            out[k] = when (v) {
                is JSONObject -> jsonObjectToMap(v)
                JSONObject.NULL -> null
                else -> v
            }
        }
        return out
    }
}
