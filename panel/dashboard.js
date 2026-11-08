const byId = (id) => document.getElementById(id);
const refreshButton = byId('refresh');
const connectionState = byId('connection-state');
let refreshing = false;
let refreshNumber = 0;
const missionTitles = new Map();
const missionTitleChecks = new Map();
const playerNameChecks = new Map();
let rosterNames = new Map();

async function refreshLabels(url, ids, key, checks) {
    const now = Date.now();
    const due = [...new Set(ids.filter(id => id && (checks.get(id) ?? 0) <= now))];
    if (!due.length) return {};
    for (const id of due) checks.set(id, now + 30000);
    const labels = await panelLabels(url, due, key);
    for (const id of due) {
        if (labels[id]) checks.set(id, now + 300000);
    }
    return labels;
}

async function command(name, data = {}) {
    const response = await panelFetch(`/command/${name}`, {
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

function renderMission(missions, refreshId) {
    const missionAvailable = missions.status === 'fulfilled';
    byId('current-mission').textContent = missionAvailable ? missions.value?.currentMission?.Key?.Name || 'No active mission' : '—';
    byId('next-mission').textContent = missionAvailable ? missions.value?.nextMission?.Key?.Name || 'None reported' : '—';
    const workshopMissions = [['current-mission', missions.value?.currentMission], ['next-mission', missions.value?.nextMission]]
        .filter(([, mission]) => mission?.Key?.Group === 'Workshop');
    for (const [id, mission] of workshopMissions) {
        if (missionTitles.has(mission.Key.Name)) byId(id).textContent = missionTitles.get(mission.Key.Name);
    }
    refreshLabels('/workshop/titles', workshopMissions.map(([, mission]) => mission.Key.Name), 'titles', missionTitleChecks).then(titles => {
        for (const [id, title] of Object.entries(titles)) missionTitles.set(id, title);
        if (refreshId !== refreshNumber) return;
        for (const [id, mission] of workshopMissions) {
            if (titles[mission.Key.Name]) byId(id).textContent = titles[mission.Key.Name];
        }
    });
}

function renderClock(time) {
    const progress = byId('mission-progress');
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
    // Retain names across a failed poll, while trimming names for players who have left.
    if (players) {
        const present = new Set(players.map(player => player?.steamId));
        for (const id of playerNameChecks.keys()) {
            if (!present.has(id)) playerNameChecks.delete(id);
        }
        rosterNames = new Map(players.map(player => [player?.steamId, player?.displayName || rosterNames.get(player?.steamId)]));
    }
    byId('player-rows').replaceChildren(...(players || []).map((player) => {
        const row = document.createElement('tr');
        row.dataset.steamId = player?.steamId ?? '';
        row.insertCell().textContent = rosterNames.get(player?.steamId) || 'Name unavailable';
        row.insertCell().textContent = player?.steamId ?? '—';
        row.insertCell().textContent = player?.faction || '—';
        return row;
    }));
    const ids = (players || []).filter(player => !player?.displayName).map(player => player?.steamId);
    refreshLabels('/players/names', ids, 'names', playerNameChecks).then(names => {
        for (const [id, name] of Object.entries(names)) {
            if (rosterNames.has(id)) rosterNames.set(id, name);
        }
        for (const row of byId('player-rows').rows) {
            if (rosterNames.has(row.dataset.steamId)) row.cells[0].textContent = rosterNames.get(row.dataset.steamId) || 'Name unavailable';
        }
    });
}

async function readStatus(name, render) {
    let result;
    try {
        result = {status: 'fulfilled', value: await command(name)};
    } catch {
        result = {status: 'rejected'};
    }
    render(result);
    return result.status === 'fulfilled';
}

async function refresh() {
    if (refreshing) return;
    refreshing = true;
    const refreshId = ++refreshNumber;
    refreshButton.disabled = true;
    try {
        const results = await Promise.all([
            readStatus('get-mission', result => renderMission(result, refreshId)),
            readStatus('get-mission-time', renderClock),
            readStatus('get-player-list', renderRoster),
        ]);
        const successes = results.filter(Boolean).length;
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
