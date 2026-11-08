const serverOptionInputs = [...document.querySelectorAll('[data-server-option]')];
const readServerOption = (input) => input.type === 'checkbox' ? input.checked : input.value;
const savedServerOptions = new Map(serverOptionInputs.map((input) => [input, readServerOption(input)]));

function serverOptionEdits() {
    const changes = {};
    for (const input of serverOptionInputs) {
        const value = readServerOption(input);
        if (value === savedServerOptions.get(input)) continue;
        const {serverOption, kind} = input.dataset;
        if (input.type === 'number') {
            if (!input.validity.valid || (kind !== 'port' && value === '')) {
                const section = input.closest('details');
                section.open = true;
                byId('advanced-server-options').open = true;
                input.reportValidity();
                byId('options-status').textContent = 'Enter a valid value for each server option you changed.';
                input.focus();
                return null;
            }
            changes[serverOption] = value === '' ? null : input.valueAsNumber;
        } else {
            changes[serverOption] = value;
        }
    }
    return changes;
}

const workshopControls = byId('workshop-controls');
const workshopStatusText = byId('workshop-status');
const workshopDetailsText = byId('workshop-details');
const workshopCheckButton = byId('check-workshop');
const workshopMods = byId('workshop-mods');
let workshopTimer;
let workshopRequest = 0;
let inspectedItem = '';
let incompatibleItem = '';
let readyWorkshopSelection = '';
let pendingWorkshopQueue;
const cancelWorkshopQueue = byId('cancel-workshop-queue');
const workshopLibrary = byId('downloaded-workshop');
const refreshLibrary = byId('refresh-workshop-library');
let availableMissions = [];

function isWorkshopLink(value) {
    return /^(?:https?:\/\/)?(?:www\.)?steamcommunity\.com\/(?:sharedfiles|workshop)\/filedetails\/?\?/i.test(value);
}

function showWorkshopTitles(titles) {
    for (const [key, title] of Object.entries(titles)) workshopTitles.set(key, title);
    for (const node of document.querySelectorAll('[data-workshop-title]')) {
        const title = workshopTitles.get(node.dataset.workshopTitle);
        if (title) node.textContent = title;
    }
    for (const option of byId('saved-workshop').children) {
        const title = workshopTitles.get(option.value);
        if (title) option.textContent = title;
    }
}

function useAvailableMission(mission, command, button) {
    if (missionWorkspace.dataset.busy === 'true') return;
    const preset = byId('preset');
    const option = [...preset.options].find(entry => entry.value === mission.name && entry.dataset.group === mission.group);
    if (option) {
        option.selected = true;
        preset.dispatchEvent(new Event('change'));
    } else {
        preset.value = '';
        byId('group').value = mission.group;
        byId('mission-name').value = mission.name;
        workshopInputChanged();
    }
    if (command === 'next') {
        queueRotationMission(button, selectedMission(), byId('library-action-status'));
    } else {
        byId(command).click();
    }
}

function renderAvailableMissions() {
    const source = byId('available-source').value;
    const list = byId('workshop-library');
    const rows = availableMissions.filter(mission => !source || mission.group === source);
    list.replaceChildren(...rows.map((mission, index) => {
        const row = document.createElement('li');
        const identity = document.createElement('div');
        const title = document.createElement('strong');
        title.id = `available-mission-${index}`;
        title.textContent = mission.title;
        if (mission.group === 'Workshop') {
            title.dataset.workshopTitle = mission.name;
            title.textContent = workshopTitles.get(mission.name) || mission.title;
        }
        const state = document.createElement('p');
        state.className = 'meta';
        const inRotation = rotation.some(entry => entry.Key?.Group === mission.group && entry.Key?.Name === mission.name);
        const sourceLabel = {Workshop: 'Downloaded Workshop', BuiltIn: 'Built-in', User: 'Local scenario'}[mission.group];
        state.textContent = `${sourceLabel}${inRotation ? ' · In rotation' : ''}`;
        const controls = document.createElement('div');
        controls.className = 'actions';
        for (const [label, command] of [['Add', 'add'], ['Queue next', 'next']]) {
            if (command === 'add' && inRotation) continue;
            const button = action(label, event => useAvailableMission(mission, command, event.currentTarget));
            button.dataset.missionAction = '';
            button.disabled = missionWorkspace.dataset.busy === 'true';
            button.setAttribute('aria-describedby', title.id);
            controls.append(button);
        }
        identity.append(title, state);
        row.append(identity, controls);
        return row;
    }));
    list.hidden = !rows.length;
    byId('workshop-library-status').textContent = rows.length ? `${rows.length} available.` : 'No missions from this source.';
}

async function loadWorkshopLibrary() {
    if (refreshLibrary.disabled) return;
    refreshLibrary.disabled = true;
    const status = byId('workshop-library-status');
    status.textContent = 'Checking available missions…';
    try {
        const response = await panelFetch(workshopLibrary.dataset.url);
        const result = await response.json();
        if (!response.ok) throw new Error(result.message);
        availableMissions = [
            ...result.built_in.map(name => ({name, title: name, group: 'BuiltIn'})),
            ...result.local.map(name => ({name, title: name, group: 'User'})),
            ...result.missions.map(mission => ({name: mission.id, title: mission.title, group: 'Workshop'})),
        ];
        renderAvailableMissions();
        const ids = result.missions.slice(0, 96).map(mission => mission.id);
        panelLabels('/workshop/titles', ids, 'titles').then(showWorkshopTitles);
    } catch (error) {
        status.textContent = error.message || 'Could not read the server’s Workshop downloads.';
    } finally {
        refreshLibrary.disabled = false;
    }
}

async function loadWorkshopTitles() {
    const keys = rotation.filter(mission => mission.Key?.Group === 'Workshop').map(mission => mission.Key.Name);
    showWorkshopTitles(await panelLabels('/workshop/titles', [...keys, ...Array.from(byId('saved-workshop').children, option => option.value)], 'titles'));
}

function showWorkshopMods(id, mods, customMap) {
    workshopMods.hidden = !mods?.required;
    const list = byId('workshop-mods-list');
    list.replaceChildren();
    if (!mods?.required) return;

    const {requirements, catalog_unavailable: unavailable} = mods;
    const manageMods = byId('review-mods');
    manageMods.href = `/mods?item=${encodeURIComponent(id)}`;
    manageMods.hidden = !requirements.length || unavailable;
    byId('workshop-source').href = `https://steamcommunity.com/sharedfiles/filedetails/?id=${encodeURIComponent(id)}`;
    const status = unavailable ? 'The NOMM catalog is unavailable. Try again to look up the listed mods.'
        : requirements.length ? 'Requirements listed by the mission author. Release links come from NOMM’s catalog. The panel does not check what’s installed.'
            : 'Steam flags a mod requirement but lists no names the panel can read. See the mission on Steam.';
    byId('workshop-mods-status').textContent = customMap
        ? `${status} The server and players need the same map build.` : status;

    for (const mod of requirements) {
        const row = document.createElement('li');
        const name = document.createElement('strong');
        name.textContent = `${mod.name}${mod.requested_version ? ` v${mod.requested_version}` : ''}`;
        row.append(name);
        const detail = document.createElement('span');
        detail.className = 'meta';
        if (mod.url) {
            const link = document.createElement('a');
            link.href = mod.url;
            link.target = '_blank';
            link.rel = 'noopener';
            link.textContent = mod.matched_version ? `${mod.package} v${mod.matched_version}` : `${mod.package} releases`;
            detail.append(link);
            if (mod.game_version) detail.append(` · Catalog game version ${mod.game_version}`);
            if (mod.requested_version && !mod.matched_version) detail.append(' · Requested version not listed');
        } else {
            detail.textContent = unavailable ? 'Catalog unavailable' : 'No matching release link in the catalog';
        }
        row.append(detail);
        list.append(row);
    }
    list.hidden = !requirements.length;
}

async function refreshWorkshop(lookup = false) {
    clearTimeout(workshopTimer);
    const request = ++workshopRequest;
    const value = byId('mission-name').value.trim();
    if (isWorkshopLink(value)) byId('group').value = 'Workshop';
    const selected = byId('group').value === 'Workshop';
    workshopControls.hidden = !selected;
    byId('next').textContent = selected && readyWorkshopSelection !== value ? 'Add and queue' : 'Queue once';
    if (value !== inspectedItem) {
        inspectedItem = value;
        incompatibleItem = '';
        workshopDetailsText.textContent = '';
        workshopMods.hidden = true;
    }
    workshopCheckButton.disabled = !value || lookup;
    if (!selected || !value) {
        workshopStatusText.textContent = 'Paste a Steam Workshop item link or ID.';
        return;
    }
    if (lookup) {
        workshopMods.hidden = true;
        workshopDetailsText.textContent = 'Checking Steam and the mod catalog…';
    }
    try {
        const query = new URLSearchParams({item: value});
        if (lookup) query.set('details', '1');
        const response = await panelFetch(`${workshopControls.dataset.url}?${query}`, {cache: 'no-store'});
        const item = await response.json();
        if (request !== workshopRequest) return;
        workshopStatusText.textContent = item.message;
        readyWorkshopSelection = ['cached', 'downloaded'].includes(item.state) ? value : '';
        byId('next').textContent = readyWorkshopSelection ? 'Queue once' : 'Add and queue';
        if (lookup) {
            const {title, message, custom_map, mods} = item.details ?? {};
            workshopDetailsText.textContent = response.ok ? title || message || '' : '';
            showWorkshopMods(item.id, mods, custom_map);
            if (title) {
                showWorkshopTitles({[item.id]: title, [value]: title});
            }
            incompatibleItem = item.details?.incompatible ? value : '';
        }
        const pending = pendingWorkshopQueue;
        if (pending && item.id === pending.mission.Key.Name) {
            if (item.state === 'cached') {
                if (missionWorkspace.dataset.busy === 'true') {
                    workshopTimer = setTimeout(refreshWorkshop, 1000);
                    return;
                }
                clearPendingWorkshopQueue();
                await sendMissionControl(byId('next'), missionWorkspace.dataset.queueUrl, pending.mission, byId('picker-status'));
                return;
            }
            if (item.state !== 'waiting' || Date.now() >= pending.until) {
                clearPendingWorkshopQueue();
                byId('picker-status').textContent = item.state === 'waiting'
                    ? 'The Workshop mission is saved. Once the download finishes, choose Queue next on its rotation row.'
                    : item.message;
            } else {
                workshopStatusText.textContent = 'Saved in the rotation. Keep this page open to queue it when the download finishes.';
            }
        }
        if (item.state === 'waiting') workshopTimer = setTimeout(refreshWorkshop, 5000);
    } catch {
        if (request !== workshopRequest) return;
        if (lookup) workshopDetailsText.textContent = '';
        workshopStatusText.textContent = 'The panel could not check Workshop status. Try again.';
        if (pendingWorkshopQueue && Date.now() < pendingWorkshopQueue.until) {
            workshopTimer = setTimeout(refreshWorkshop, 5000);
        } else if (pendingWorkshopQueue) {
            clearPendingWorkshopQueue();
            byId('picker-status').textContent = 'The Workshop mission is saved. Check the download and try queueing it again.';
        }
    } finally {
        if (request === workshopRequest) workshopCheckButton.disabled = false;
    }
}

function workshopInputChanged() {
    readyWorkshopSelection = '';
    if (pendingWorkshopQueue) {
        clearPendingWorkshopQueue();
        byId('picker-status').textContent = 'Automatic queue canceled. The Workshop mission is saved in the rotation.';
    }
    clearTimeout(workshopTimer);
    workshopRequest += 1;
    if (byId('mission-name').value.trim() !== inspectedItem) {
        workshopMods.hidden = true;
    }
    if (isWorkshopLink(byId('mission-name').value.trim())) byId('group').value = 'Workshop';
    workshopControls.hidden = byId('group').value !== 'Workshop';
    byId('next').textContent = workshopControls.hidden ? 'Queue once' : 'Add and queue';
    workshopTimer = setTimeout(refreshWorkshop, 250);
}

function clearPendingWorkshopQueue() {
    pendingWorkshopQueue = undefined;
    cancelWorkshopQueue.hidden = true;
}

async function handleWorkshopAction(button, mission, queue) {
    const status = byId('picker-status');
    if (incompatibleItem && mission.Key.Name === incompatibleItem) {
        status.textContent = workshopDetailsText.textContent;
        return;
    }
    if (missionWorkspace.dataset.busy === 'true') return;
    clearPendingWorkshopQueue();
    status.textContent = 'Saving the Workshop mission…';
    const selection = JSON.stringify(mission);
    const result = await sendMissionControl(button, '/settings/workshop', {mission, revision}, status);
    if (!result) {
        if (selection === JSON.stringify(selectedMission())) workshopStatusText.textContent = status.textContent;
        return;
    }

    const saved = result.mission;
    const inDraft = rotation.some((entry) => entry.Key?.Group === 'Workshop' && entry.Key.Name === saved.Key.Name);
    if (!inDraft) rotation.push(saved);
    showRotation();
    updateMissionOptions('saved-workshop', 'Workshop', result.workshop_items);
    loadWorkshopTitles();
    if (selection !== JSON.stringify(selectedMission())) return;
    workshopStatusText.textContent = result.message;
    if (queue && result.state === 'cached') {
        await sendMissionControl(button, missionWorkspace.dataset.queueUrl, saved, status);
    } else if (result.state === 'waiting') {
        if (queue) {
            pendingWorkshopQueue = {mission: saved, until: Date.now() + 120000};
            cancelWorkshopQueue.hidden = false;
            status.textContent = 'Saved to the rotation. Keep this page open to queue it when the download finishes.';
        }
        refreshWorkshop();
    }
}

const missionWorkspace = byId('mission-rotation');
const switchDialog = byId('switch-mission-dialog');
let switchTarget;

function missionControlButtons() {
    return [...['add', 'next', 'save', 'save-options'].map(byId), ...document.querySelectorAll('[data-mission-action]')];
}

async function sendMissionControl(button, url, payload, status) {
    if (missionWorkspace.dataset.busy === 'true') return;
    missionWorkspace.dataset.busy = 'true';
    missionControlButtons().forEach(control => { control.disabled = true; });
    try {
        return await send(button, url, payload, status);
    } finally {
        delete missionWorkspace.dataset.busy;
        missionControlButtons().forEach(control => { control.disabled = false; });
    }
}

function validMissionAction(mission, status) {
    if (!mission.Key?.Name || !Number.isFinite(mission.MaxTime ?? 7200) || (mission.MaxTime ?? 7200) <= 0) {
        status.textContent = 'Enter a mission name and a positive time limit.';
        return false;
    }
    if (mission.Key.Group === 'Workshop' && mission.Key.Name === incompatibleItem) {
        status.textContent = workshopDetailsText.textContent;
        return false;
    }
    return true;
}

function queueRotationMission(button, mission, status = byId('status')) {
    if (missionWorkspace.dataset.busy === 'true' || !validMissionAction(mission, status)) return;
    clearPendingWorkshopQueue();
    return sendMissionControl(button, missionWorkspace.dataset.queueUrl, mission, status);
}

function reviewMissionSwitch(button, mission) {
    if (missionWorkspace.dataset.busy === 'true' || !validMissionAction(mission, byId('status'))) return;
    const name = (mission.Key.Group === 'Workshop' && workshopTitles.get(mission.Key.Name)) || mission.Key.Name;
    byId('switch-mission-help').textContent = `End the current mission and load ${name} next? The server’s normal delay between missions applies.`;
    switchTarget = {button, mission: {...mission, Key: {...mission.Key}}};
    switchDialog.returnValue = '';
    switchDialog.showModal();
}

switchDialog.addEventListener('close', () => {
    const target = switchTarget;
    switchTarget = undefined;
    if (!target || switchDialog.returnValue !== 'confirm' || missionWorkspace.dataset.busy === 'true') return;
    clearPendingWorkshopQueue();
    const delay = byId('switch-delay').valueAsNumber;
    const announcement = byId('switch-message').value;
    if (!Number.isInteger(delay) || delay < 0 || delay > 3600) return;
    try { localStorage.setItem('mission-switch-options', JSON.stringify({delay, announcement})); } catch { /* storage is optional */ }
    byId('status').textContent = 'Requesting a mission switch…';
    sendMissionControl(target.button, missionWorkspace.dataset.switchUrl, {...target.mission, delay, announcement});
});

try {
    const saved = JSON.parse(localStorage.getItem('mission-switch-options'));
    if (Number.isInteger(saved?.delay) && saved.delay >= 0 && saved.delay <= 3600) byId('switch-delay').value = saved.delay;
    if (typeof saved?.announcement === 'string') byId('switch-message').value = saved.announcement.slice(0, 240);
} catch { /* use the form defaults */ }

function updateMissionOptions(id, group, items) {
    const options = byId(id);
    const selected = byId('preset').selectedOptions[0];
    const previous = selected?.dataset.group === group ? selected.value : '';
    options.replaceChildren(...items.map((name) => {
        const option = new Option(group === 'Workshop' ? workshopTitles.get(name) || `Workshop item ${name}` : name, name);
        option.dataset.group = group;
        option.selected = name === previous;
        return option;
    }));
    options.hidden = !items.length;
}

byId('mission-name').addEventListener('input', workshopInputChanged);
byId('mission-time').addEventListener('input', workshopInputChanged);
byId('group').addEventListener('change', workshopInputChanged);
byId('preset').addEventListener('change', workshopInputChanged);
cancelWorkshopQueue.addEventListener('click', () => {
    clearPendingWorkshopQueue();
    byId('picker-status').textContent = 'Automatic queue canceled. The Workshop mission is saved in the rotation.';
    refreshWorkshop();
});
workshopCheckButton.addEventListener('click', () => refreshWorkshop(true));
refreshLibrary.addEventListener('click', loadWorkshopLibrary);
byId('available-source').addEventListener('change', renderAvailableMissions);
document.addEventListener('rotation-rendered', renderAvailableMissions);
loadWorkshopLibrary();
document.addEventListener('settings-saved', ({detail: {result, options}}) => {
    for (const input of serverOptionInputs) {
        const key = input.dataset.serverOption;
        if (Object.hasOwn(options, key)) {
            const value = options[key];
            savedServerOptions.set(input, input.type === 'checkbox' ? value : String(value ?? ''));
        }
    }
    updateMissionOptions('local-missions', 'User', result.local_missions);
    updateMissionOptions('saved-workshop', 'Workshop', result.workshop_items);
    loadWorkshopTitles();
    loadWorkshopLibrary();
    refreshWorkshop();
});
loadWorkshopTitles();
