const output = document.getElementById('server-log');
const status = document.getElementById('log-status');
const refreshButton = document.getElementById('log-refresh');
const pauseButton = document.getElementById('log-pause');
let paused = false;

function jumpToLatest() {
    output.scrollTop = output.scrollHeight;
}

async function refreshLogs() {
    if (refreshButton.disabled) return;
    refreshButton.disabled = true;
    try {
        const response = await fetch(output.dataset.url, {signal: AbortSignal.timeout(5000)});
        if (!response.ok) throw new Error('Log unavailable');
        const log = await response.json();
        const follow = output.scrollHeight - output.scrollTop - output.clientHeight < 8;
        if (output.textContent !== log.text) {
            output.textContent = log.text;
            if (follow) jumpToLatest();
        }
        if (log.text) {
            status.textContent = `Showing up to 64 KiB · Checked at ${new Date().toLocaleTimeString()}`;
        } else {
            status.textContent = log.available
                ? 'The log file is empty. Waiting for server output.'
                : 'No game log yet. Check that the game container is running the current setup.';
        }
    } catch {
        status.textContent = 'Could not read the latest server output.';
    } finally {
        refreshButton.disabled = false;
    }
}

function refreshIfActive() {
    if (!paused && !document.hidden) refreshLogs();
}

refreshButton.addEventListener('click', refreshLogs);
pauseButton.addEventListener('click', () => {
    paused = !paused;
    pauseButton.setAttribute('aria-pressed', String(paused));
    pauseButton.textContent = paused ? 'Resume updates' : 'Pause updates';
    refreshIfActive();
});
document.getElementById('log-latest').addEventListener('click', jumpToLatest);
document.addEventListener('visibilitychange', refreshIfActive);
setInterval(refreshIfActive, 3000);
refreshLogs();
