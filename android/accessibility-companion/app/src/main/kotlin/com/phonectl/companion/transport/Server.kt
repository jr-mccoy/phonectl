package com.phonectl.companion.transport

import java.io.BufferedReader
import java.io.BufferedWriter
import java.io.IOException
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException

/**
 * Loopback NDJSON TCP server (foreground-service SPEC §1/§2/§9).
 *
 * - Binds 127.0.0.1 only; refuses to start on any non-loopback host.
 * - Per-connection handler thread; reads newline-delimited JSON requests, writes
 *   newline-delimited JSON responses. Connections are long-lived (multiple requests).
 * - Defensively drops any accepted peer that is not a loopback address.
 * - Closes idle connections after [idleTimeoutMs] (default 30s) to avoid resource leaks.
 * - Never logs request payloads — method names + outcomes only.
 */
class Server(
    private val port: Int,
    private val dispatcher: Dispatcher,
    private val host: String = "127.0.0.1",
    private val idleTimeoutMs: Int = 30_000,
) {

    @Volatile
    private var running = false
    private var serverSocket: ServerSocket? = null
    private var acceptThread: Thread? = null

    val boundPort: Int
        get() = serverSocket?.localPort ?: port

    fun start() {
        require(host in LOOPBACK) { "companion server is loopback-only; refusing host '$host'" }
        if (running) return
        val ss = ServerSocket()
        ss.reuseAddress = true
        ss.bind(InetSocketAddress(InetAddress.getByName(host), port))
        serverSocket = ss
        running = true
        acceptThread = Thread({ acceptLoop(ss) }, "phonectl-companion-accept").apply {
            isDaemon = true
            start()
        }
    }

    private fun acceptLoop(ss: ServerSocket) {
        while (running) {
            val client = try {
                ss.accept()
            } catch (e: IOException) {
                if (running) continue else break
            }
            val addr = client.inetAddress
            if (addr == null || !addr.isLoopbackAddress) {
                runCatching { client.close() }
                continue
            }
            Thread({ handle(client) }, "phonectl-companion-conn").apply {
                isDaemon = true
                start()
            }
        }
    }

    private fun handle(client: Socket) {
        client.soTimeout = idleTimeoutMs
        client.use { sock ->
            val reader = BufferedReader(InputStreamReader(sock.getInputStream(), Charsets.UTF_8))
            val writer = BufferedWriter(OutputStreamWriter(sock.getOutputStream(), Charsets.UTF_8))
            try {
                while (running) {
                    val line = reader.readLine() ?: break
                    val response = dispatcher.handleLine(line) ?: continue
                    writer.write(response)
                    writer.write("\n")
                    writer.flush()
                }
            } catch (e: SocketTimeoutException) {
                // idle timeout reached — close the connection
            } catch (e: IOException) {
                // client disconnected
            }
        }
    }

    fun stop() {
        running = false
        runCatching { serverSocket?.close() }
        serverSocket = null
    }

    companion object {
        val LOOPBACK = setOf("127.0.0.1", "localhost", "::1")
        const val DEFAULT_PORT = 8765
    }
}
