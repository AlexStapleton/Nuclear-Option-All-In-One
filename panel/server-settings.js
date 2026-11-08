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
        const response = await fetch(`${workshopControls.dataset.url}?${query}`, {cache: 'no-store'});
        const item = await response.json();
        if (request !== workshopRequest) return;
        workshopStatusText.textContent = item.message;
        if (lookup && response.ok) {
            workshopDetailsText.textContent = item.details.title || item.details.message || '';
            incompatibleItem = item.details.incompatible ? value : '';
        }
        if (item.state === 'waiting') workshopTimer = setTimeout(refreshWorkshop, 5000);
    } catch {
        if (request !== workshopRequest) return;
        workshopStatusText.textContent = 'The panel could not check Workshop status. Try again.';
    } finally {
        if (request === workshopRequest) workshopCheckButton.disabled = false;
    }
}

function workshopInputChanged() {
    clearTimeout(workshopTimer);
    workshopRequest += 1;
    if (isWorkshopLink(byId('mission-name').value.trim())) byId('group').value = 'Workshop';
    workshopControls.hidden = byId('group').value !== 'Workshop';
    workshopTimer = setTimeout(refreshWorkshop, 250);
}

function knownIncompatibleWorkshop(event) {
    if (byId('group').value !== 'Workshop' || !incompatibleItem
            || byId('mission-name').value.trim() !== incompatibleItem) return;
    event.stopImmediatePropagation();
    byId('status').textContent = workshopDetailsText.textContent;
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
byId('group').addEventListener('change', workshopInputChanged);
byId('preset').addEventListener('change', workshopInputChanged);
byId('add').addEventListener('click', knownIncompatibleWorkshop, true);
byId('next').addEventListener('click', knownIncompatibleWorkshop, true);
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
