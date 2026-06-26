package com.phonectl.companion.service

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityEvent
import com.phonectl.companion.state.TrustState
import com.phonectl.companion.transport.Method

/** The AccessibilityService methods. Built out in Task 5. */
class CompanionAccessibilityService : AccessibilityService() {

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}

    override fun onInterrupt() {}

    override fun onDestroy() {
        if (instance === this) instance = null
        super.onDestroy()
    }

    companion object {
        @Volatile
        var instance: CompanionAccessibilityService? = null
            private set

        /**
         * The AccessibilityService-backed method handlers, plugged into the foreground service's
         * dispatcher. Handlers resolve [instance] lazily so they work regardless of service start
         * order. Populated in Task 5.
         */
        fun methods(state: TrustState): Map<String, Method> = emptyMap()
    }
}
