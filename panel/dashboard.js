const byId = (id) => document.getElementById(id);
const refreshButton = byId('refresh');
const connectionState = byId('connection-state');
let refreshing = false;

async function command(name, data = {}) {
    const response = await fetch(`/command/${name}`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data),
        signal: AbortSignal.timeout(5000),
    });
    const result = await response.json();
    if (!response.ok || result.status_code !== 'Success') {
        throw new Error(result.status_code || 'Server command failed');
    }
    return result.response;
}

function clock(seconds) {
    const whole = Math.floor(seconds);
    const minutes = Math.floor(whole / 60);
    return `${minutes}:${String(whole % 60).padStart(2, '0')}`;
}

function renderMission(missions, time) {
    const missionAvailable = missions.status === 'fulfilled';
    const progress = byId('mission-progress');
    byId('current-mission').textContent = missionAvailable ? missions.value?.currentMission?.Key?.Name || 'No active mission' : '—';
    byId('next-mission').textContent = missionAvailable ? missions.value?.nextMission?.Key?.Name || 'None reported' : '—';
    const elapsed = time.value?.currentTime;
    const limit = time.value?.maxTime;
    const hasClock = Number.isFinite(elapsed) && elapsed >= 0 && Number.isFinite(limit) && limit > 0;
    byId('mission-clock').textContent = hasClock ? `${clock(elapsed)} elapsed / ${clock(limit)} limit`
        : time.status === 'fulfilled' ? 'Mission timer idle' : 'Mission timer unavailable';
    progress.hidden = !hasClock;
    progress.value = hasClock ? Math.min(100, elapsed / limit * 100) : 0;
}

function renderRoster(result) {
    const players = result.status === 'fulfilled' && Array.isArray(result.value?.Players) ? result.value.Players : null;
    byId('player-count').textContent = players?.length ?? '—';
    byId('roster').hidden = !players?.length;
    const message = byId('roster-status');
    message.hidden = Boolean(players?.length);
    message.textContent = players ? 'No players connected.' : 'Player list unavailable.';
    byId('player-rows').replaceChildren(...(players || []).map((player) => {
        const row = document.createElement('tr');
        row.insertCell().textContent = player?.steamId ?? '—';
        row.insertCell().textContent = player?.faction || '—';
        return row;
    }));
}

async function refresh() {
    if (refreshing) return;
    refreshing = true;
    refreshButton.disabled = true;
    try {
        const results = await Promise.allSettled([
            command('get-mission'), command('get-mission-time'), command('get-player-list'),
        ]);
        renderMission(results[0], results[1]);
        renderRoster(results[2]);
        const successes = results.filter((result) => result.status === 'fulfilled').length;
        connectionState.dataset.state = successes === results.length ? 'online' : successes ? 'partial' : 'offline';
        connectionState.textContent = successes === results.length ? 'Server responding' : successes ? 'Partial response' : 'Server unreachable';
        byId('last-check').textContent = `Checked at ${new Date().toLocaleTimeString()}`;
    } finally {
        refreshing = false;
        refreshButton.disabled = false;
    }
}

byId('broadcast').addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = event.submitter;
    const input = byId('message');
    const status = byId('broadcast-status');
    const message = input.value.trim();
    if (!message) {
        status.textContent = 'Enter a message first.';
        return;
    }
    button.disabled = true;
    try {
        await command('send-chat-message', {message});
        if (input.value.trim() === message) input.value = '';
        status.textContent = 'Announcement sent.';
    } catch {
        status.textContent = 'The server did not confirm delivery.';
    } finally {
        button.disabled = false;
    }
});

refreshButton.addEventListener('click', refresh);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
setInterval(() => { if (!document.hidden) refresh(); }, 15000);
refresh();
