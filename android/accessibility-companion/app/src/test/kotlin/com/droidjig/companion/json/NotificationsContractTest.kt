package com.droidjig.companion.json

import com.droidjig.companion.transport.MethodException
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Cross-language contract: the Kotlin `notifications_list` serialization must equal the Python
 * authority fixture tests/test_providers_notifications.py::COMPANION_RAW field-for-field. Pure
 * (no Android) so it runs on the JVM. Reply/dismiss resolver tests are added alongside their tasks.
 */
class NotificationsContractTest {

    // tests/test_providers_notifications.py::COMPANION_RAW
    private val companionRaw = JSONObject(
        """
        {"key":"0|com.msg|42|tag|10123","package":"com.msg",
         "title":"Alice","text":"see you at 6?","category":"msg","post_time":1718900000000,
         "actions":[{"title":"Reply","remote_input":true},{"title":"Mark read"}]}
        """.trimIndent()
    )

    private val alice = NotifData(
        key = "0|com.msg|42|tag|10123",
        pkg = "com.msg",
        title = "Alice",
        text = "see you at 6?",
        category = "msg",
        postTime = 1718900000000L,
        actions = listOf(
            NotifAction(title = "Reply", remoteInput = true),
            NotifAction(title = "Mark read", remoteInput = false),
        ),
    )

    @Test
    fun serializedNotificationEqualsCompanionRawFieldForField() {
        val serialized = Notifications.notification(alice)
        // org.json similar() compares maps/arrays recursively, treating an absent key as distinct
        // from one set to false — so this proves the omit-when-false action shape too.
        assertTrue(
            "serialized=$serialized expected=$companionRaw",
            serialized.similar(companionRaw),
        )
    }

    @Test
    fun listWrapsNotificationsUnderKey() {
        val data = Notifications.list(listOf(alice))
        val arr = data.getJSONArray("notifications")
        assertEquals(1, arr.length())
        assertTrue(arr.getJSONObject(0).similar(companionRaw))
    }

    @Test
    fun absentCategorySerializesAsJsonNull() {
        val n = Notifications.notification(alice.copy(category = null))
        assertTrue(n.isNull("category"))
    }

    @Test
    fun actionWithoutRemoteInputOmitsTheKey() {
        val a = Notifications.action(NotifAction("Mark read", remoteInput = false))
        assertTrue("remote_input must be absent when false", !a.has("remote_input"))
        assertEquals("Mark read", a.getString("title"))
    }

    @Test
    fun postTimeSerializesAsLongEpochMs() {
        val n = Notifications.notification(alice)
        assertEquals(1718900000000L, n.getLong("post_time"))
    }

    // --- notifications_reply resolver (Task 3) ---

    @Test
    fun replyActionIndexFindsFirstRemoteInputAction() {
        // A leading non-reply action proves "first action with RemoteInput" wins (index 1, not 0).
        // Distinct key so the resolver selects this notification, not `alice`.
        val laterReply = alice.copy(
            key = "1|com.msg|99|tag|10123",
            actions = listOf(
                NotifAction("Mark read", remoteInput = false),
                NotifAction("Reply", remoteInput = true),
            )
        )
        assertEquals(1, Notifications.replyActionIndex(listOf(alice, laterReply), laterReply.key))
        assertEquals(0, Notifications.replyActionIndex(listOf(alice), alice.key))
    }

    @Test
    fun replyActionIndexNotFoundForUnknownKey() {
        val e = runCatching { Notifications.replyActionIndex(listOf(alice), "nope") }.exceptionOrNull()
        assertTrue("$e", e is MethodException)
        assertEquals("not_found", (e as MethodException).code)
    }

    @Test
    fun replyActionIndexNoRemoteInputWhenNoActionHasIt() {
        val noReply = alice.copy(actions = listOf(NotifAction("Mark read", remoteInput = false)))
        val e = runCatching { Notifications.replyActionIndex(listOf(noReply), noReply.key) }
            .exceptionOrNull()
        assertTrue("$e", e is MethodException)
        assertEquals("no_remote_input", (e as MethodException).code)
    }

    // --- notifications_dismiss resolver (Task 4) ---

    @Test
    fun requireActivePassesWhenKeyPresent() {
        Notifications.requireActive(listOf(alice), alice.key) // no throw
    }

    @Test
    fun requireActiveNotFoundWhenKeyAbsent() {
        val e = runCatching { Notifications.requireActive(listOf(alice), "gone") }.exceptionOrNull()
        assertTrue("$e", e is MethodException)
        assertEquals("not_found", (e as MethodException).code)
    }
}
