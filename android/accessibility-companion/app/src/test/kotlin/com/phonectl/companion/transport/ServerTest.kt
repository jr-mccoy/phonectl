package com.phonectl.companion.transport

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.InetSocketAddress
import java.net.Socket

/**
 * Drives the real loopback Server the way the Python SocketTransport does:
 * one NDJSON request line in, one NDJSON response line out, request_id echoed.
 */
class ServerTest {

    private fun withServer(methods: Map<String, Method>, body: (Int) -> Unit) {
        val server = Server(port = 0, dispatcher = Dispatcher(methods))
        server.start()
        try {
            body(server.boundPort)
        } finally {
            server.stop()
        }
    }

    private fun roundTrip(port: Int, requestLine: String): String? {
        Socket().use { sock ->
            sock.connect(InetSocketAddress("127.0.0.1", port), 2000)
            sock.soTimeout = 2000
            val writer = OutputStreamWriter(sock.getOutputStream(), Charsets.UTF_8)
            val reader = BufferedReader(InputStreamReader(sock.getInputStream(), Charsets.UTF_8))
            writer.write(requestLine)
            writer.write("\n")
            writer.flush()
            return reader.readLine()
        }
    }

    @Test
    fun loopbackRoundTripEchoesRequestId() {
        val methods = mapOf<String, Method>(
            "ping" to { _ -> JSONObject().put("pong", true) }
        )
        withServer(methods) { port ->
            val req = JSONObject().put("version", 1).put("request_id", "deadbeef")
                .put("method", "ping").put("params", JSONObject()).put("timeout", 2.0).toString()
            val raw = roundTrip(port, req)!!
            val resp = JSONObject(raw)
            assertEquals("deadbeef", resp.getString("request_id"))
            assertTrue(resp.getBoolean("ok"))
            assertTrue(resp.getJSONObject("data").getBoolean("pong"))
        }
    }

    @Test
    fun multipleRequestsOverOneConnection() {
        val methods = mapOf<String, Method>(
            "ping" to { _ -> JSONObject().put("pong", true) }
        )
        withServer(methods) { port ->
            Socket().use { sock ->
                sock.connect(InetSocketAddress("127.0.0.1", port), 2000)
                sock.soTimeout = 2000
                val writer = OutputStreamWriter(sock.getOutputStream(), Charsets.UTF_8)
                val reader = BufferedReader(InputStreamReader(sock.getInputStream(), Charsets.UTF_8))
                for (id in listOf("a", "b", "c")) {
                    val req = JSONObject().put("version", 1).put("request_id", id)
                        .put("method", "ping").put("params", JSONObject()).toString()
                    writer.write(req); writer.write("\n"); writer.flush()
                    val resp = JSONObject(reader.readLine())
                    assertEquals(id, resp.getString("request_id"))
                }
            }
        }
    }

    @Test
    fun nonJsonLineEmitsNoResponseThenValidLineSucceeds() {
        val methods = mapOf<String, Method>(
            "ping" to { _ -> JSONObject().put("pong", true) }
        )
        withServer(methods) { port ->
            Socket().use { sock ->
                sock.connect(InetSocketAddress("127.0.0.1", port), 2000)
                sock.soTimeout = 2000
                val writer = OutputStreamWriter(sock.getOutputStream(), Charsets.UTF_8)
                val reader = BufferedReader(InputStreamReader(sock.getInputStream(), Charsets.UTF_8))
                // garbage line -> no response emitted (silently dropped)
                writer.write("garbage\n"); writer.flush()
                val req = JSONObject().put("version", 1).put("request_id", "x")
                    .put("method", "ping").put("params", JSONObject()).toString()
                writer.write(req); writer.write("\n"); writer.flush()
                val resp = JSONObject(reader.readLine())
                assertEquals("x", resp.getString("request_id"))
            }
        }
    }

    @Test
    fun refusesNonLoopbackHost() {
        val server = Server(port = 0, dispatcher = Dispatcher(emptyMap()), host = "10.0.0.5")
        assertThrows(IllegalArgumentException::class.java) { server.start() }
    }

    @Test
    fun idleConnectionIsClosedAfterTimeout() {
        // SPEC §9: an idle connection is closed after idleTimeoutMs. Drive a short timeout and
        // assert the server-side EOF (readLine -> null) arrives without the client sending anything.
        val server = Server(port = 0, dispatcher = Dispatcher(emptyMap()), idleTimeoutMs = 300)
        server.start()
        try {
            Socket().use { sock ->
                sock.connect(InetSocketAddress("127.0.0.1", server.boundPort), 2000)
                sock.soTimeout = 4000 // client read timeout well past the 300ms server idle close
                val reader = BufferedReader(InputStreamReader(sock.getInputStream(), Charsets.UTF_8))
                // Send nothing — the server should time out, close the socket, and the client sees EOF.
                assertNull(reader.readLine())
            }
        } finally {
            server.stop()
        }
    }
}
