const byId = (id) => document.getElementById(id);
const steamId = byId('steam-id');
const operation = byId('player-operation');
const review = byId('action-review');
const confirmButton = byId('action-confirm');
const roster = byId('moderation-roster');
const rosterStatus = byId('moderation-roster-status');
const refreshButton = byId('refresh-roster');
const actionButtons = document.querySelectorAll('[data-operation]');
const operations = {
    'kick-player': {
        label: 'Kick player',
        detail: 'This disconnects the player and blocks rejoining until the server restarts or the kick is undone.',
        danger: true,
    },
    'banlist-add': {
        label: 'Ban player',
        detail: 'This adds the Steam ID to the active ban list and the first configured ban file.',
        danger: true,
    },
    'unkick-player': {
        label: 'Undo kick',
        detail: 'This removes the Steam ID from the kick list.',
    },
    'banlist-remove': {
        label: 'Remove ban',
        detail: 'This removes the Steam ID from the active ban list and the first configured ban file.',
    },
    'banlist-reload': {
        label: 'Reload bans',
        detail: 'Add bans from the configured files to the active list?',
        status: 'list-status',
    },
    'clear-kicked-players': {
        label: 'Clear all kicks',
        detail: 'Allow every kicked player without an active ban to rejoin?',
        status: 'list-status',
        danger: true,
    },
    'banlist-clear': {
        label: 'Clear active bans',
        detail: 'Remove every active ban from memory? The server can load bans from disk again.',
        status: 'list-status',
        danger: true,
    },
    'update-ready': {
        label: 'Send update-ready signal',
        detail: 'Send the update-ready signal to the running server?',
        status: 'maintenance-status',
    },
};
let rosterRequest = 0;
let reviewedAction;
let running = false;

async function requestCommand(name, data = {}) {
    const response = await fetch(`/command/${name}`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data),
        signal: AbortSignal.timeout(5000),
    });
    const result = await response.json().catch(() => ({error: `HTTP ${response.status}`}));
    return {ok: response.ok && result.status_code === 'Success', result};
}

function updateSelection() {
    for (const button of roster.querySelectorAll('button')) {
        button.setAttribute('aria-pressed', String(button.dataset.steamId === steamId.value));
    }
}

function makePlayerRow(player) {
    const row = document.createElement('tr');
    const id = typeof player?.steamId === 'string' ? player.steamId : '';
    row.insertCell().textContent = id || 'Unavailable';
    row.insertCell().textContent = player?.faction || '—';
    const action = row.insertCell();
    if (/^[0-9]{1,20}$/.test(id)) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = 'Select';
        button.dataset.steamId = id;
        button.setAttribute('aria-label', `Select player ${id}`);
        button.addEventListener('click', () => {
            steamId.value = id;
            updateSelection();
            operation.focus();
        });
        action.append(button);
    }
    return row;
}

async function refreshRoster() {
    const request = ++rosterRequest;
    refreshButton.disabled = true;
    try {
        const {ok, result} = await requestCommand('get-player-list');
        if (request !== rosterRequest) return;
        const players = result.response?.Players;
        if (!ok || !Array.isArray(players)) throw new Error('Roster unavailable');
        byId('moderation-players').replaceChildren(...players.map(makePlayerRow));
        roster.hidden = !players.length;
        rosterStatus.textContent = players.length
            ? `${players.length} connected · Select a player to fill in their Steam ID.`
            : 'No players connected. You can still enter a Steam ID manually.';
        updateSelection();
    } catch {
        if (request !== rosterRequest) return;
        roster.hidden = true;
        rosterStatus.textContent = 'Could not load connected players. You can still enter a Steam ID manually.';
    } finally {
        if (request === rosterRequest) refreshButton.disabled = false;
    }
}

function updatePlayerOperation() {
    byId('player-action-help').textContent = operations[operation.value].detail;
    byId('ban-reason-field').hidden = operation.value !== 'banlist-add';
}

function reviewAction(action) {
    if (running || review.open) return;
    reviewedAction = action;
    review.returnValue = '';
    byId('action-review-heading').textContent = action.label;
    byId('action-review-text').textContent = action.detail;
    confirmButton.textContent = action.label;
    confirmButton.classList.toggle('danger', Boolean(action.danger));
    review.showModal();
}

async function runAction(action) {
    running = true;
    actionButtons.forEach((button) => { button.disabled = true; });
    const status = byId(action.status);
    status.textContent = `${action.label}…`;
    status.dataset.state = '';
    const raw = byId('response-area');
    byId('command-label').textContent = `${action.label} · ${new Date().toLocaleTimeString()}`;
    try {
        const {ok, result} = await requestCommand(action.name, action.data);
        raw.textContent = JSON.stringify(result, null, 2);
        if (!ok) {
            status.dataset.state = 'error';
            status.textContent = `${action.label} failed · ${result.error || result.status_code || 'Unknown response'}`;
        } else {
            status.dataset.state = 'success';
            status.textContent = `${action.label} succeeded.`;
            if (action.data?.steam_id) refreshRoster();
        }
    } catch {
        status.dataset.state = 'error';
        status.textContent = 'The server did not confirm the action. Check its state before retrying.';
        raw.textContent = 'No response received.';
    } finally {
        raw.hidden = false;
        running = false;
        actionButtons.forEach((button) => { button.disabled = false; });
    }
}

review.addEventListener('close', () => {
    if (review.returnValue === 'confirm' && reviewedAction && !running) runAction(reviewedAction);
    reviewedAction = undefined;
});

byId('player-action').addEventListener('submit', (event) => {
    event.preventDefault();
    const name = operation.value;
    const action = operations[name];
    const data = {steam_id: steamId.value};
    const reason = byId('ban-reason').value.trim();
    if (name === 'banlist-add' && reason) data.reason = reason;
    reviewAction({...action, name, data, status: 'player-action-status',
        detail: `Steam ID ${data.steam_id}. ${action.detail}`});
});

document.querySelectorAll('[data-command]').forEach((button) => {
    button.addEventListener('click', () => {
        const name = button.dataset.command;
        reviewAction({...operations[name], name});
    });
});

byId('mission-timer').addEventListener('submit', (event) => {
    event.preventDefault();
    const time = byId('time-remaining').valueAsNumber;
    reviewAction({name: 'set-time-remaining', label: 'Set mission timer', status: 'maintenance-status',
        detail: `Set the current mission to ${time} seconds remaining?`, data: {time}});
});

byId('reload-config').addEventListener('submit', (event) => {
    event.preventDefault();
    const path = byId('config-path').value.trim();
    reviewAction({name: 'reload-config', label: 'Reload config', status: 'maintenance-status', data: {path},
        detail: path ? `Switch the running server to the config at ${path}?` : 'Reload the active server config?'});
});

operation.addEventListener('change', updatePlayerOperation);
steamId.addEventListener('input', updateSelection);
refreshButton.addEventListener('click', refreshRoster);
updatePlayerOperation();
refreshRoster();
