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
let workshopTimer;
let workshopRequest = 0;
let inspectedItem = '';
let incompatibleItem = '';
let pendingWorkshopQueue;
const cancelWorkshopQueue = byId('cancel-workshop-queue');

function isWorkshopLink(value) {
    return /^(?:https?:\/\/)?(?:www\.)?steamcommunity\.com\/(?:sharedfiles|workshop)\/filedetails\/?\?/i.test(value);
}

async function refreshWorkshop(lookup = false) {
    clearTimeout(workshopTimer);
    const request = ++workshopRequest;
    const value = byId('mission-name').value.trim();
    if (isWorkshopLink(value)) byId('group').value = 'Workshop';
    const selected = byId('group').value === 'Workshop';
    workshopControls.hidden = !selected;
    if (value !== inspectedItem) {
        inspectedItem = value;
        incompatibleItem = '';
        workshopDetailsText.textContent = '';
    }
    workshopCheckButton.disabled = !value || lookup;
    if (!selected || !value) {
        workshopStatusText.textContent = 'Paste a Steam Workshop item link or ID.';
        return;
    }
    if (lookup) workshopDetailsText.textContent = 'Checking Steam…';
    try {
        const query = new URLSearchParams({item: value});
        if (lookup) query.set('details', '1');
        const response = await panelFetch(`${workshopControls.dataset.url}?${query}`, {cache: 'no-store'});
        const item = await response.json();
        if (request !== workshopRequest) return;
        workshopStatusText.textContent = item.message;
        if (lookup && response.ok) {
            const {title, message, custom_map, mod_required} = item.details;
            const requirement = custom_map ? 'Requires the NOCustomMaps mod and the matching map on the server.'
                : mod_required ? 'Steam marks this mission as requiring a mod.' : '';
            workshopDetailsText.textContent = [title || message, requirement].filter(Boolean).join(' · ');
            if (title) {
                workshopTitles.set(item.id, title);
                showRotation();
            }
            incompatibleItem = item.details.incompatible ? value : '';
        }
        const pending = pendingWorkshopQueue;
        if (pending && item.id === pending.mission.Key.Name) {
            if (item.state === 'cached') {
                clearPendingWorkshopQueue();
                await send(byId('next'), '/settings/next', pending.mission);
                return;
            }
            if (item.state !== 'waiting' || Date.now() >= pending.until) {
                clearPendingWorkshopQueue();
                byId('status').textContent = item.state === 'waiting'
                    ? 'The Workshop mission is saved. Once the download finishes, click Queue as next mission again.'
                    : item.message;
            } else {
                workshopStatusText.textContent = 'Saved in the rotation. Keep this page open to queue it when the download finishes.';
            }
        }
        if (item.state === 'waiting') workshopTimer = setTimeout(refreshWorkshop, 5000);
    } catch {
        if (request !== workshopRequest) return;
        workshopStatusText.textContent = 'The panel could not check Workshop status. Try again.';
        if (pendingWorkshopQueue && Date.now() < pendingWorkshopQueue.until) {
            workshopTimer = setTimeout(refreshWorkshop, 5000);
        } else if (pendingWorkshopQueue) {
            clearPendingWorkshopQueue();
            byId('status').textContent = 'The Workshop mission is saved. Check the download and try queueing it again.';
        }
    } finally {
        if (request === workshopRequest) workshopCheckButton.disabled = false;
    }
}

function workshopInputChanged() {
    if (pendingWorkshopQueue) {
        clearPendingWorkshopQueue();
        byId('status').textContent = 'Automatic queue canceled. The Workshop mission is saved in the rotation.';
    }
    clearTimeout(workshopTimer);
    workshopRequest += 1;
    if (isWorkshopLink(byId('mission-name').value.trim())) byId('group').value = 'Workshop';
    workshopControls.hidden = byId('group').value !== 'Workshop';
    workshopTimer = setTimeout(refreshWorkshop, 250);
}

function clearPendingWorkshopQueue() {
    pendingWorkshopQueue = undefined;
    cancelWorkshopQueue.hidden = true;
}

async function handleWorkshopAction(button, mission, queue) {
    if (incompatibleItem && mission.Key.Name === incompatibleItem) {
        byId('status').textContent = workshopDetailsText.textContent;
        return;
    }
    clearPendingWorkshopQueue();
    const otherButtons = ['save', 'save-options', queue ? 'add' : 'next'].map(byId);
    if (otherButtons.some((control) => control.disabled)) return;
    otherButtons.forEach((control) => { control.disabled = true; });
    byId('status').textContent = 'Saving the Workshop mission…';
    const selection = JSON.stringify(mission);
    let result;
    try {
        result = await send(button, '/settings/workshop', {mission, revision});
    } finally {
        otherButtons.forEach((control) => { control.disabled = false; });
    }
    if (!result) {
        if (selection === JSON.stringify(selectedMission())) workshopStatusText.textContent = byId('status').textContent;
        return;
    }

    const saved = result.mission;
    const inDraft = rotation.some((entry) => entry.Key?.Group === 'Workshop' && entry.Key.Name === saved.Key.Name);
    if (!inDraft) rotation.push(saved);
    showRotation();
    updateMissionOptions('saved-workshop', 'Workshop', result.workshop_items);
    if (selection !== JSON.stringify(selectedMission())) return;
    workshopStatusText.textContent = result.message;
    if (queue && result.state === 'cached') {
        await send(button, '/settings/next', saved);
    } else if (result.state === 'waiting') {
        if (queue) {
            pendingWorkshopQueue = {mission: saved, until: Date.now() + 120000};
            cancelWorkshopQueue.hidden = false;
            byId('status').textContent = 'Saved to the rotation. Keep this page open to queue it when the download finishes.';
        }
        refreshWorkshop();
    }
}

function updateMissionOptions(id, group, items) {
    const options = byId(id);
    const selected = byId('preset').selectedOptions[0];
    const previous = selected?.dataset.group === group ? selected.value : '';
    options.replaceChildren(...items.map((name) => {
        const option = new Option(group === 'Workshop' ? `Workshop item ${name}` : name, name);
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
    byId('status').textContent = 'Automatic queue canceled. The Workshop mission is saved in the rotation.';
    refreshWorkshop();
});
workshopCheckButton.addEventListener('click', () => refreshWorkshop(true));
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
    refreshWorkshop();
});
