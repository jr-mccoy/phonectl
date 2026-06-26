package com.phonectl.companion.service

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityEvent

/** The AccessibilityService methods. Built out in Task 5. */
class CompanionAccessibilityService : AccessibilityService() {
    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}
    override fun onInterrupt() {}
}
