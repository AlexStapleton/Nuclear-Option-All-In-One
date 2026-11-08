async function panelFetch(url, options = {}) {
    const headers = new Headers(options.headers);
    if (!['GET', 'HEAD', 'OPTIONS'].includes((options.method || 'GET').toUpperCase())) {
        headers.set('X-CSRF-Token', document.querySelector('meta[name="panel-csrf"]').content);
    }
    const response = await fetch(url, {...options, headers});
    if (response.status === 401) document.getElementById('session-expired').hidden = false;
    return response;
}

async function panelLabels(url, ids, key) {
    const query = new URLSearchParams();
    for (const id of new Set(ids.filter(Boolean))) query.append('id', id);
    if (!query.size) return {};
    try {
        const response = await panelFetch(`${url}?${query}`);
        return response.ok ? (await response.json())[key] || {} : {};
    } catch {
        return {};
    }
}

function resolvePlayerNames(players) {
    const ids = players.filter(player => !player?.displayName).map(player => player?.steamId);
    return panelLabels('/players/names', ids, 'names');
}
