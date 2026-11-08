const output = document.getElementById('server-log');
const status = document.getElementById('log-status');
const refreshButton = document.getElementById('log-refresh');
const pauseButton = document.getElementById('log-pause');
const source = document.getElementById('log-source');
let paused = false;

function jumpToLatest() {
    output.scrollTop = output.scrollHeight;
}

async function refreshLogs() {
    if (refreshButton.disabled) return;
    const selected = source.value;
    refreshButton.disabled = true;
    try {
        const query = new URLSearchParams({source: selected});
        const response = await panelFetch(`${output.dataset.url}?${query}`, {signal: AbortSignal.timeout(5000)});
        if (!response.ok) throw new Error('Log unavailable');
        const log = await response.json();
        if (selected !== source.value) return;
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
                : selected === 'mods'
                    ? 'No BepInEx log for the selected server mod set. Check Game server for startup messages.'
                    : 'No game log yet. Check that the game container is running the current setup.';
        }
    } catch {
        if (selected === source.value) status.textContent = 'Could not read the selected log.';
    } finally {
        refreshButton.disabled = false;
        if (selected !== source.value) refreshLogs();
    }
}

function refreshIfActive() {
    if (!paused && !document.hidden) refreshLogs();
}

refreshButton.addEventListener('click', refreshLogs);
source.addEventListener('change', () => {
    output.textContent = '';
    status.textContent = 'Loading selected log…';
    refreshLogs();
});
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
