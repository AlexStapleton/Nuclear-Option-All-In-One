(() => {
    const get = id => document.getElementById(id);
    const section = get('launch-settings');
    const prefs = get('launcher-preferences');
    const perf = get('launch-performance');
    const profileForm = get('launch-profile-form');
    const profileStatus = get('launch-profile-status');
    const dialog = get('launch-profile-dialog');
    const preset = get('launch-fps-preset');
    const fps = get('launch-fps');
    const restartButtons = [...section.querySelectorAll('[data-restart]')];
    const restartDialog = get('launch-restart-dialog');
    const history = get('launch-history');
    const restartStatus = get('launch-restart-status');
    let restartMode;
    let restartPending = false;
    let lastRefresh = 0;
    let historyLoading = false;
    let pendingProfile;
    let busy = false;

    function setPreset() {
        const custom = preset.value === 'custom';
        get('launch-fps-field').hidden = !custom;
        fps.disabled = !custom;
    }

    function format(value, unit = '') {
        return value == null ? '—' : `${value}${unit}`;
    }

    function renderStatus({saved, active, plugin, sample, restart}) {
        restartPending = Boolean(restart.pending);
        for (const button of restartButtons) button.disabled = !restart.available || restartPending;
        restartStatus.textContent = {
            queued: 'Restart queued.',
            stopping: 'Stopping the game…',
            updating: 'SteamCMD is checking for and applying updates…',
            starting: 'Starting the game…',
            launched: 'The game process has started.',
            failed: 'Restart failed. See Server logs for details.',
            superseded: 'The game restarted before it could apply this request.',
        }[restart.phase] || (restart.available ? '' : 'Restart control becomes available after the launcher starts.');
        const pending = active && Object.keys(saved).some(key => active[key] !== saved[key]);
        get('launch-active').textContent = active
            ? `Last launch · ${active.fps} FPS cap · ${active.log_files} game logs × ${active.log_mib} MiB${pending ? ' · Saved changes pending restart' : ''}`
            : 'The launcher has yet to report its settings.';
        if (plugin) {
            get('launch-plugin-status').textContent = plugin.available
                ? plugin.overrides_fps
                    ? 'The NukeStats FPS override or frame pacer can supersede the launch cap during play.'
                    : 'NukeStats is available in the selected mod set.'
                : plugin.installed
                    ? 'NukeStats was found. Start the mod set and check that its version generates the Perf settings.'
                    : 'Optional. Add a compatible NukeStats plugin to the selected BepInEx mod set to tune physics workers and measure frame times.';
        }
        get('launch-sample').textContent = sample
            ? `Measured ${sample.fps} FPS · Average ${format(sample.frame_ms, ' ms')} · 95th percentile ${format(sample.p95_ms, ' ms')} · Workers ${format(sample.workers)} / ${format(sample.workers_max)} · Sample ${sample.age}s ago`
            : 'No recent sample. Enable frame timing and wait about a minute after starting the game.';
    }

    async function loadHistory() {
        if (!history.open || historyLoading) return;
        historyLoading = true;
        const status = get('launch-history-status');
        try {
            const response = await panelFetch(history.dataset.url, {signal: AbortSignal.timeout(5000)});
            const data = await response.json();
            if (!response.ok) throw new Error(data.message);
            const rows = get('launch-history-rows');
            rows.replaceChildren();
            for (const sample of data.samples) {
                const row = rows.insertRow();
                const values = [
                    new Date(sample.timestamp * 1000).toLocaleString(),
                    sample.mission || 'Unreported',
                    format(sample.fps),
                    [sample.frame_ms, sample.p95_ms, sample.worst_ms].map(value => format(value)).join(' / '),
                    [sample.players, sample.ai].map(value => format(value)).join(' / '),
                    format(sample.workers),
                ];
                for (const value of values) row.insertCell().textContent = value;
            }
            get('launch-history-table').hidden = !data.samples.length;
            status.textContent = data.samples.length ? `${data.samples.length} recent samples`
                : 'Enable NukeStats frame timing to collect samples.';
        } catch {
            status.textContent = 'Could not load performance history.';
        } finally {
            historyLoading = false;
        }
    }

    function reviewProfile(action, name) {
        pendingProfile = {action, name};
        const restore = action === 'apply-profile';
        get('launch-profile-dialog-title').textContent = `${restore ? 'Restore' : 'Delete'} ${name}?`;
        get('launch-profile-dialog-message').textContent = restore
            ? 'This replaces the saved server configuration. Restart the game to apply it.'
            : 'This deletes the saved profile.';
        get('launch-profile-confirm').textContent = restore ? 'Restore saved configuration' : 'Delete profile';
        dialog.returnValue = '';
        dialog.showModal();
    }

    function showProfiles(profiles) {
        const list = get('launch-profiles');
        list.replaceChildren();
        for (const profile of profiles) {
            const row = document.createElement('li');
            const identity = document.createElement('div');
            const name = document.createElement('strong');
            name.textContent = profile.name;
            const description = document.createElement('p');
            description.className = 'meta';
            description.textContent = [profile.server_name, profile.matches ? 'Matches saved configuration' : ''].filter(Boolean).join(' · ');
            identity.append(name, description);
            const actions = document.createElement('div');
            actions.className = 'actions';
            for (const [label, action] of [['Restore', 'apply-profile'], ['Delete', 'delete-profile']]) {
                const button = document.createElement('button');
                button.type = 'button';
                button.textContent = label;
                button.setAttribute('aria-label', `${label} ${profile.name}`);
                button.addEventListener('click', () => reviewProfile(action, profile.name));
                actions.append(button);
            }
            row.append(identity, actions);
            list.append(row);
        }
        list.hidden = !profiles.length;
    }

    async function refresh(loadFields = false, loadProfiles = false) {
        lastRefresh = Date.now();
        try {
            const url = loadFields || loadProfiles ? section.dataset.url : `${section.dataset.url}?sample=1`;
            const response = await panelFetch(url, {signal: AbortSignal.timeout(5000)});
            const data = await response.json();
            if (!response.ok) throw new Error(data.message);
            renderStatus(data);
            if (loadFields) {
                preset.value = [30, 60].includes(data.saved.fps) ? String(data.saved.fps) : 'custom';
                fps.value = data.saved.fps;
                setPreset();
                get('launch-log-size').value = data.saved.log_mib;
                get('launch-log-files').value = data.saved.log_files;
                get('launch-workers').value = data.plugin.workers;
                get('launch-timing').checked = data.plugin.timing;
                perf.hidden = !data.plugin.available;
                for (const form of [prefs, profileForm]) form.querySelector('button').disabled = false;
            }
            if (loadFields || loadProfiles) showProfiles(data.profiles);
        } catch (error) {
            get('launch-active').textContent = error.message || 'Could not read launch settings.';
        }
    }

    async function save(data, status) {
        if (!data || busy) return false;
        busy = true;
        status.textContent = 'Saving…';
        try {
            const response = await panelFetch(section.dataset.url, {method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data), signal: AbortSignal.timeout(8000)});
            const result = await response.json();
            status.textContent = result.message;
            if (!response.ok) return false;
            if (result.reload) {
                try { sessionStorage.setItem('startup-profile-status', result.message); } catch {}
                location.reload();
            } else {
                await refresh(false, data.action.endsWith('profile'));
            }
            return true;
        } catch {
            status.textContent = 'Could not save launch settings. Refresh to check what was saved.';
            return false;
        } finally {
            busy = false;
        }
    }

    preset.addEventListener('change', setPreset);
    prefs.addEventListener('submit', event => {
        event.preventDefault();
        save({action: 'preferences', fps: preset.value === 'custom' ? fps.valueAsNumber : Number(preset.value),
            log_mib: get('launch-log-size').valueAsNumber, log_files: get('launch-log-files').valueAsNumber},
            get('launch-preferences-status'));
    });
    perf.addEventListener('submit', event => {
        event.preventDefault();
        save({action: 'performance', workers: get('launch-workers').valueAsNumber,
            timing: get('launch-timing').checked}, get('launch-performance-status'));
    });
    profileForm.addEventListener('submit', async event => {
        event.preventDefault();
        const name = get('launch-profile-name');
        if (await save({action: 'save-profile', name: name.value.trim()}, profileStatus)) name.value = '';
    });
    dialog.addEventListener('close', () => {
        if (dialog.returnValue === 'confirm') save(pendingProfile, profileStatus);
        pendingProfile = undefined;
    });
    for (const button of restartButtons) {
        button.addEventListener('click', () => {
            restartMode = button.dataset.restart;
            get('launch-restart-title').textContent = button.textContent;
            get('launch-restart-message').textContent = restartMode === 'update'
                ? 'This disconnects players and checks for Steam updates before starting the game.'
                : 'This disconnects players and starts the installed build with your saved launch settings.';
            restartDialog.returnValue = '';
            restartDialog.showModal();
        });
    }
    restartDialog.addEventListener('close', () => {
        if (restartDialog.returnValue === 'confirm') save({action: 'restart', mode: restartMode}, restartStatus);
    });
    history.addEventListener('toggle', loadHistory);
    try {
        profileStatus.textContent = sessionStorage.getItem('startup-profile-status') || '';
        sessionStorage.removeItem('startup-profile-status');
    } catch {}
    refresh(true);
    setInterval(() => {
        if (!document.hidden && !busy && (restartPending || Date.now() - lastRefresh >= 15000)) {
            refresh();
            loadHistory();
        }
    }, 3000);
})();
