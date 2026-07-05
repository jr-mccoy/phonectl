package com.phonectl.companion.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.preference.Preference
import androidx.preference.PreferenceCategory
import androidx.preference.PreferenceFragmentCompat
import androidx.preference.SwitchPreferenceCompat
import com.phonectl.companion.R
import com.phonectl.companion.service.CompanionForegroundService
import com.phonectl.companion.service.CompanionNotificationListenerService
import com.phonectl.companion.state.SharedPrefsTrustState

/**
 * Per-capability toggles (foreground-service SPEC §6) + the emergency-stop control + a Trust &
 * Safety text section (foreground-service SPEC §7). Switch preferences persist into the same
 * SharedPreferences file [SharedPrefsTrustState] reads, with keys matching its `cap_<key>` /
 * `stopped` scheme, so the handshake reflects the UI immediately.
 */
class SettingsActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (savedInstanceState == null) {
            supportFragmentManager.beginTransaction()
                .replace(android.R.id.content, SettingsFragment())
                .commit()
        }
    }

    class SettingsFragment : PreferenceFragmentCompat() {

        /** The notification-access hint, refreshed in onResume after the user returns from Settings. */
        private var notifAccessPref: Preference? = null

        override fun onCreatePreferences(savedInstanceState: Bundle?, rootKey: String?) {
            val ctx = preferenceManager.context
            preferenceManager.sharedPreferencesName = SharedPrefsTrustState.PREFS
            preferenceManager.sharedPreferencesMode = Context.MODE_PRIVATE

            val screen = preferenceManager.createPreferenceScreen(ctx)

            // --- Capabilities (default-enabled per SPEC §6) ---
            val capabilities = PreferenceCategory(ctx).apply {
                title = getString(R.string.prefs_capabilities_title)
            }
            screen.addPreference(capabilities)
            for ((key, labelRes) in CAPABILITY_LABELS) {
                capabilities.addPreference(
                    SwitchPreferenceCompat(ctx).apply {
                        this.key = "cap_$key"
                        title = getString(labelRes)
                        setDefaultValue(true)
                    }
                )
            }

            // --- Pairing token (Finding 2) ---
            // Loopback is not an app boundary on Android; the token is the real gate. Show it so
            // the user can paste it into `phonectl config` (companion_token), and copy on tap.
            val state = SharedPrefsTrustState(ctx)
            val pairing = PreferenceCategory(ctx).apply {
                title = getString(R.string.prefs_pairing_title)
            }
            screen.addPreference(pairing)
            pairing.addPreference(
                Preference(ctx).apply {
                    title = getString(R.string.pref_pairing_token_title)
                    summary = state.companionToken()
                    isPersistent = false
                    setOnPreferenceClickListener {
                        val clip = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                        clip.setPrimaryClip(
                            ClipData.newPlainText("phonectl companion_token", state.companionToken()))
                        Toast.makeText(ctx, R.string.pref_pairing_token_copied, Toast.LENGTH_SHORT).show()
                        true
                    }
                }
            )

            // --- Notification access (grant guidance) ---
            val notifSetup = PreferenceCategory(ctx).apply {
                title = getString(R.string.prefs_notif_setup_title)
            }
            screen.addPreference(notifSetup)
            notifAccessPref = Preference(ctx).apply {
                title = getString(R.string.pref_notif_access_title)
                isPersistent = false
                setOnPreferenceClickListener {
                    runCatching {
                        startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
                    }
                    true
                }
            }
            notifSetup.addPreference(notifAccessPref!!)

            // --- Emergency stop ---
            val control = PreferenceCategory(ctx).apply {
                title = getString(R.string.prefs_control_title)
            }
            screen.addPreference(control)
            control.addPreference(
                SwitchPreferenceCompat(ctx).apply {
                    key = SharedPrefsTrustState.KEY_STOPPED
                    title = getString(R.string.pref_stopped_title)
                    summaryOn = getString(R.string.pref_stopped_on)
                    summaryOff = getString(R.string.pref_stopped_off)
                    setDefaultValue(false)
                    setOnPreferenceChangeListener { _, newValue ->
                        val stopped = newValue as Boolean
                        CompanionForegroundService.send(
                            ctx,
                            if (stopped) CompanionForegroundService.ACTION_STOP
                            else CompanionForegroundService.ACTION_RESUME,
                        )
                        true
                    }
                }
            )

            // --- Trust & Safety (read-only text) ---
            val trust = PreferenceCategory(ctx).apply {
                title = getString(R.string.prefs_trust_title)
            }
            screen.addPreference(trust)
            for ((titleRes, summaryRes) in TRUST_SECTIONS) {
                trust.addPreference(
                    Preference(ctx).apply {
                        title = getString(titleRes)
                        summary = getString(summaryRes)
                        isSelectable = false
                        isPersistent = false
                    }
                )
            }

            preferenceScreen = screen
        }

        override fun onResume() {
            super.onResume()
            // Grant state can change while the user is in system Settings — refresh on return.
            notifAccessPref?.summary = getString(
                if (CompanionNotificationListenerService.isAccessGranted(requireContext()))
                    R.string.pref_notif_access_granted
                else
                    R.string.pref_notif_access_missing
            )
        }

        companion object {
            // Order mirrors foreground-service SPEC §6 / Capabilities.ALL_KEYS.
            private val CAPABILITY_LABELS = listOf(
                "observe_ui_native" to R.string.cap_observe_ui_native,
                "observe_ui_events" to R.string.cap_observe_ui_events,
                "act_gesture_native" to R.string.cap_act_gesture_native,
                "act_set_text_native" to R.string.cap_act_set_text_native,
                "act_semantic_action" to R.string.cap_act_semantic_action,
                "launch_app" to R.string.cap_launch_app,
                "observe_notifications" to R.string.cap_observe_notifications,
                "notifications_wait" to R.string.cap_notifications_wait,
                "notifications_reply" to R.string.cap_notifications_reply,
                "notifications_dismiss" to R.string.cap_notifications_dismiss,
                "observe_ocr" to R.string.cap_observe_ocr,
                "observe_ocr_screen" to R.string.cap_observe_ocr_screen,
                "observe_screenshot" to R.string.cap_observe_screenshot,
            )

            private val TRUST_SECTIONS = listOf(
                R.string.trust_read_title to R.string.trust_read_summary,
                R.string.trust_control_title to R.string.trust_control_summary,
                R.string.trust_notif_title to R.string.trust_notif_summary,
                R.string.trust_ocr_title to R.string.trust_ocr_summary,
                R.string.trust_local_title to R.string.trust_local_summary,
                R.string.trust_audit_title to R.string.trust_audit_summary,
                R.string.trust_warn_title to R.string.trust_warn_summary,
            )
        }
    }
}
