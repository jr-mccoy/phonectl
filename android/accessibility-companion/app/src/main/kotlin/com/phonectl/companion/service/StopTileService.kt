package com.phonectl.companion.service

import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import com.phonectl.companion.state.SharedPrefsTrustState

/**
 * Quick-Settings tile that flips the `stopped` flag (foreground-service SPEC §5). It does NOT
 * start or stop the foreground service — it only toggles the same flag the "Stop phonectl"
 * notification controls, then refreshes the service notification to match.
 *
 * - Active (not stopped): tile is STATE_ACTIVE.
 * - Inactive (stopped): tile is STATE_INACTIVE (greyed).
 */
class StopTileService : TileService() {

    private val state: SharedPrefsTrustState by lazy { SharedPrefsTrustState(this) }

    override fun onStartListening() {
        super.onStartListening()
        refreshTile()
    }

    override fun onClick() {
        super.onClick()
        val nowStopped = !state.isStoppedFlag()
        state.setStopped(nowStopped)
        // Mirror the change in the persistent notification (Stop <-> Resume).
        CompanionForegroundService.send(
            this,
            if (nowStopped) CompanionForegroundService.ACTION_STOP
            else CompanionForegroundService.ACTION_RESUME,
        )
        refreshTile()
    }

    private fun refreshTile() {
        val tile = qsTile ?: return
        val stopped = state.isStoppedFlag()
        tile.state = if (stopped) Tile.STATE_INACTIVE else Tile.STATE_ACTIVE
        tile.updateTile()
    }
}
