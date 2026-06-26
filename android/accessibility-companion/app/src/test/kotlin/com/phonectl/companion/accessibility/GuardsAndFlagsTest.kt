package com.phonectl.companion.accessibility

import com.phonectl.companion.state.ActionGate
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
