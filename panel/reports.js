const liveReport = document.getElementById('live-report');
const liveStatus = document.getElementById('live-report-status');
const liveDetails = document.getElementById('live-report-details');
const liveTitle = document.getElementById('live-report-title');
const liveMeta = document.getElementById('live-report-meta');
const liveFinished = document.getElementById('live-report-finished');
const liveRows = liveDetails.querySelector('tbody');
const reportCounters = [...liveDetails.querySelectorAll('[data-counter]')].map(cell => cell.dataset.counter);
let previousReport;
let displayedSnapshot;
let checkingReport = false;
let reportSessionExpired = false;

function showReportStatus(message) {
    if (liveStatus.textContent !== message) liveStatus.textContent = message;
}

const collectorMessages = {
    off: 'Live collection is off. Run sh install-after-action.sh on the Linux host, then restart the current build.',
    mod_set: 'A gameplay mod set is running. This build uses the after-action collector in its separate server-only profile.',
    restart: 'Collection is enabled, but this game process started without it. Restart the current build after the round.',
    rebuild: 'The collector files are incomplete or the game build changed. Run sh install-after-action.sh, then restart the current build.',
    unconfirmed: 'The running server has not confirmed collector startup. Check Server logs for “After-action collector ready.”',
    ready: 'The collector is ready. Waiting for the game to report its mission status.',
    waiting: 'The collector is ready. Waiting for a mission to start.',
    missing_snapshot: 'A mission is running, but the collector has not sent a recent scoreboard. Check Server logs for after-action errors.',
};

function showLiveReport(report, state) {
    liveDetails.hidden = !report;
    if (!report) {
        showReportStatus(collectorMessages[state] ?? collectorMessages.unconfirmed);
        return;
    }
    showReportStatus('Live · updates about every five seconds');
    if (displayedSnapshot === report.updated_utc) return;
    displayedSnapshot = report.updated_utc;
    liveTitle.textContent = report.mission;
    const minutes = Math.floor(report.duration / 60);
    const seconds = report.duration % 60;
    liveMeta.textContent = `${minutes} min ${seconds} sec · ${report.players.length} players tracked this mission`;
    const rows = report.players.map(player => {
        const row = document.createElement('tr');
        const name = document.createElement('th');
        name.scope = 'row';
        const id = document.createElement('small');
        id.className = 'meta';
        id.textContent = player.steam_id;
        name.append(player.name, document.createElement('br'), id);
        row.append(name);
        for (const key of reportCounters) row.insertCell().textContent = player[key];
        return row;
    });
    liveRows.replaceChildren(...rows);
}

async function refreshLiveReport() {
    if (document.hidden || checkingReport || reportSessionExpired) return;
    checkingReport = true;
    try {
        const url = new URL(liveReport.dataset.url, location.href);
        if (previousReport) url.searchParams.set('previous', previousReport);
        const response = await panelFetch(url);
        if (response.status === 401) {
            reportSessionExpired = true;
            liveDetails.hidden = true;
            showReportStatus('Sign in again to resume live updates.');
            return;
        }
        if (!response.ok) throw new Error('Live update failed');
        const {report, state, finished} = await response.json();
        showLiveReport(report, state);
        if (finished) {
            liveFinished.querySelector('a').href = finished;
            liveFinished.hidden = false;
        }
        if (report) previousReport = report.id;
    } catch {
        showReportStatus('Live update unavailable. Retrying…');
    } finally {
        checkingReport = false;
    }
}

refreshLiveReport();
setInterval(refreshLiveReport, 5000);
document.addEventListener('visibilitychange', refreshLiveReport);
