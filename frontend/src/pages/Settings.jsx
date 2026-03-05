
import { useEffect } from 'react'
import AutomationPanel from '../components/AutomationPanel.jsx'
import JobProgress from '../components/JobProgress.jsx'
import { useJobStatus } from '../hooks/useJobStatus.js'

export default function Settings() {
  const {
    indexStatus,
    cacheStatus,
    enrichStatus,
    discoverStatus,
    playlistStatus,
    downloadStatus,
    startPolling,
  } = useJobStatus()

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold text-[var(--text-primary)]" style={{ fontFamily: 'Syne' }}>
          Settings
        </h1>
        <p className="text-sm text-[var(--text-secondary)] mt-1">
          Scheduler configuration and automation.
        </p>
      </div>

      <JobProgress
        indexStatus={indexStatus}
        cacheStatus={cacheStatus}
        enrichStatus={enrichStatus}
        discoverStatus={discoverStatus}
        playlistStatus={playlistStatus}
        downloadStatus={downloadStatus}
      />

      <AutomationPanel
        jobStatuses={{ indexStatus, cacheStatus, enrichStatus, discoverStatus, playlistStatus, downloadStatus }}
        onTrigger={startPolling}
      />
    </div>
  )
}
