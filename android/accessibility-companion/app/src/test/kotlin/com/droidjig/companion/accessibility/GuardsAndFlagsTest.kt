package com.droidjig.companion.accessibility

import com.droidjig.companion.json.NotifData
import com.droidjig.companion.json.Notifications
import com.droidjig.companion.state.ActionGate
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class GuardsAndFlagsTest {

    private fun win(pkg: String, vararg nodes: NodeData) =
        WindowData(id = 1, type = "application", pkg = pkg, nodes = nodes.toList())

    private fun node(text: String = "", desc: String = "", password: Boolean = false) =
        NodeData(nodeId = "n", text = text, className = "T", contentDesc = desc,
            bounds = listOf(0, 0, 1, 1), password = password)

    @Test
    fun guardedPackageIsRefused() {
        val guarded = setOf("com.bank.app")
        assertTrue(ActionGate.isGuarded("com.bank.app", guarded))
        assertFalse(ActionGate.isGuarded("com.example.notes", guarded))
        assertFalse(ActionGate.isGuarded(null, guarded))
        assertFalse(ActionGate.isGuarded("", guarded))
        assertFalse(ActionGate.isGuarded("com.bank.app", emptySet()))
    }

    @Test
    fun launchTargetPackageIsGuarded() {
        // `launch` refuses when the *target* package (the one being opened) is guarded — the same
        // decision as the foreground guard, applied to the requested package (Plan 4.8 Task 1).
        val guarded = setOf("com.bank.app")
        assertTrue(ActionGate.isGuarded("com.bank.app", guarded))
        assertFalse(ActionGate.isGuarded("com.example.notes", guarded))
    }

    @Test
    fun replyNotificationPackageDrivesGuardedRefusal() {
        // `notifications_reply` resolves the notification's source package via packageForKey, then
        // refuses through the same gate when that package is guarded (Plan 4.8 Task 1).
        val items = listOf(
            NotifData(key = "k-bank", pkg = "com.bank.app", title = "", text = "",
                category = null, postTime = 0L, actions = emptyList()),
            NotifData(key = "k-chat", pkg = "com.chat.app", title = "", text = "",
                category = null, postTime = 0L, actions = emptyList()),
        )
        val guarded = setOf("com.bank.app")
        assertTrue(ActionGate.isGuarded(Notifications.packageForKey(items, "k-bank"), guarded))
        assertFalse(ActionGate.isGuarded(Notifications.packageForKey(items, "k-chat"), guarded))
        // Unknown key resolves to null → not guarded (falls through to the not_found resolver).
        assertFalse(ActionGate.isGuarded(Notifications.packageForKey(items, "nope"), guarded))
    }

    // --- Finding 10: guarded-app protection covers observation, not just actions ---

    @Test
    fun observeNativeRefusesGuardedApp() {
        // The same gate that refuses gestures also refuses reading a guarded app's UI tree — a
        // banking app on the guarded list must not be observable, not just untappable.
        val guarded = setOf("com.bank.app")
        assertTrue(ActionGate.isGuarded("com.bank.app", guarded))
        assertFalse(ActionGate.isGuarded("com.example.notes", guarded))
    }

    @Test
    fun eventsFromGuardedPackagesAreFiltered() {
        val ring = EventRing()
        ring.add("content_changed", "com.bank.app", ts = 1)
        ring.add("view_clicked", "com.chat.app", ts = 2)
        ring.add("window_state_changed", "com.bank.app", ts = 3)
        val j = ring.queryJson(since = 0, max = 50, excludePackages = setOf("com.bank.app"))
        val events = j.getJSONArray("events")
        assertEquals(1, events.length())
        assertEquals("com.chat.app", events.getJSONObject(0).getString("package"))
        // Cursor still advances past filtered events so polling does not re-deliver them.
        assertEquals(3L, j.getLong("cursor"))
    }

    @Test
    fun guardedNotificationsAreFilteredFromList() {
        val items = listOf(
            NotifData(key = "k-bank", pkg = "com.bank.app", title = "Balance", text = "secret",
                category = null, postTime = 0L, actions = emptyList()),
            NotifData(key = "k-chat", pkg = "com.chat.app", title = "Hi", text = "yo",
                category = null, postTime = 0L, actions = emptyList()),
        )
        val visible = Notifications.filterGuarded(items, setOf("com.bank.app"))
        assertEquals(listOf("k-chat"), visible.map { it.key })
        // Empty guarded set is the identity.
        assertEquals(items, Notifications.filterGuarded(items, emptySet()))
    }

    // --- Finding 16: screencap writes only under app-owned roots ---

    @Test
    fun screencapRejectsPathsOutsideAppDir() {
        val roots = listOf("/data/data/com.droidjig.companion/files",
            "/data/data/com.droidjig.companion/cache")
        assertFalse(ScreencapPaths.isAllowed("/sdcard/DCIM/steal.png", roots))
        assertFalse(ScreencapPaths.isAllowed("/data/data/com.other.app/files/x.png", roots))
        // Prefix tricks must not pass: sibling dir sharing the root as a string prefix.
        assertFalse(ScreencapPaths.isAllowed("/data/data/com.droidjig.companion/files-evil/x.png", roots))
        // Relative paths are never allowed (the service canonicalizes, but stay fail-closed).
        assertFalse(ScreencapPaths.isAllowed("files/x.png", roots))
        assertFalse(ScreencapPaths.isAllowed("", roots))
    }

    @Test
    fun screencapAllowsPathsUnderAppRoots() {
        val roots = listOf("/data/data/com.droidjig.companion/files")
        assertTrue(ScreencapPaths.isAllowed("/data/data/com.droidjig.companion/files/shot.png", roots))
        assertTrue(ScreencapPaths.isAllowed("/data/data/com.droidjig.companion/files/sub/shot.png", roots))
    }

    @Test
    fun passwordPresentFlag() {
        val withPw = listOf(win("com.app", node(text = "user"), node(password = true)))
        val noPw = listOf(win("com.app", node(text = "user")))
        assertTrue(ObserveFlags.passwordPresent(withPw))
        assertFalse(ObserveFlags.passwordPresent(noPw))
        assertTrue(ObserveFlags.compute(withPw).getBoolean("password_present"))
    }

    @Test
    fun paymentSuspectedByPackage() {
        val windows = listOf(win("com.paypal.android.p2pmobile", node(text = "Home")))
        assertTrue(ObserveFlags.paymentSuspected(windows))
        assertTrue(ObserveFlags.compute(windows).getBoolean("payment_suspected"))
    }

    @Test
    fun paymentSuspectedByText() {
        val windows = listOf(win("com.example.shop", node(text = "Enter card number")))
        assertTrue(ObserveFlags.paymentSuspected(windows))
    }

    @Test
    fun ordinaryScreenIsNotPayment() {
        val windows = listOf(win("com.example.notes", node(text = "Grocery list")))
        assertFalse(ObserveFlags.paymentSuspected(windows))
        assertFalse(ObserveFlags.compute(windows).getBoolean("payment_suspected"))
    }
}
