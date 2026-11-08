const byId = id => document.getElementById(id);
const reviewMods = byId('review-mods');
const installMods = byId('install-mods');
const vanillaMods = byId('vanilla-mods');
const modPlanBox = byId('workshop-mod-plan');
const modStatus = byId('mod-install-status');
const modUrl = byId('server-mods').dataset.url;
let modPlanId;
let modReviewRequest = 0;
let modBusy = false;
let modPoll;
let inventory;
let inventoryRequest = 0;
const modPage = byId('mod-management');

function resetModPlan() {
    modReviewRequest += 1;
    modPlanId = undefined;
    modPlanBox.hidden = true;
    installMods.disabled = true;
    reviewMods.disabled = false;
}

async function modRequest(data) {
    const response = await panelFetch(modUrl, data ? {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data),
    } : {cache: 'no-store'});
    const result = await response.json();
    if (!response.ok) throw new Error(result.message);
    return result;
}

async function updateModStatus() {
    clearTimeout(modPoll);
    try {
        const {selected, running, message} = await modRequest();
        const completed = modBusy && !running;
        byId('selected-mods').textContent = selected ? `Selected mod set: ${selected}` : 'No panel mod set selected.';
        vanillaMods.hidden = !selected;
        vanillaMods.disabled = running;
        modBusy = running;
        installMods.disabled = running || !modPlanId;
        if (message) modStatus.textContent = message;
        if (running) modPoll = setTimeout(updateModStatus, 2500);
        if (completed) loadInventory();
    } catch (error) {
        byId('selected-mods').textContent = error.message || 'Could not check the selected mod set.';
        if (modBusy) modPoll = setTimeout(updateModStatus, 5000);
    }
}

async function reviewModPlan(overrides = {}) {
    const target = byId('mod-item').value.trim();
    if (!target) return;
    const request = ++modReviewRequest;
    const problems = byId('workshop-mod-problems');
    reviewMods.disabled = installMods.disabled = true;
    modPlanId = undefined;
    modPlanBox.hidden = false;
    problems.textContent = 'Building the install plan…';
    byId('workshop-mod-assets').replaceChildren();
    byId('workshop-mod-choices').replaceChildren();
    try {
        const plan = await modRequest({action: 'preview', item: target, overrides});
        if (request !== modReviewRequest) return;
        problems.textContent = plan.problems.join(' ');
        for (const choice of plan.choices) {
            const label = document.createElement('label');
            label.className = 'field';
            label.textContent = `${choice.name} replacement for v${choice.requested}`;
            const select = document.createElement('select');
            select.append(new Option('Choose a version', ''));
            for (const version of choice.versions) select.append(new Option(version, version));
            select.value = plan.overrides[choice.id] || '';
            select.addEventListener('change', () => {
                const next = {...plan.overrides};
                if (select.value) next[choice.id] = select.value;
                else delete next[choice.id];
                reviewModPlan(next);
            });
            label.append(select);
            byId('workshop-mod-choices').append(label);
        }
        for (const asset of plan.assets) {
            const row = document.createElement('li');
            row.textContent = `${asset.name} v${asset.version}${asset.game_version ? ` · Catalog game version ${asset.game_version}` : ''}`;
            byId('workshop-mod-assets').append(row);
        }
        if (!plan.problems.length) modPlanId = plan.id;
        installMods.disabled = modBusy || !modPlanId;
    } catch (error) {
        if (request === modReviewRequest) problems.textContent = error.message || 'Could not build the install plan.';
    } finally {
        if (request === modReviewRequest) reviewMods.disabled = false;
    }
}

async function changeModSet(action) {
    installMods.disabled = vanillaMods.disabled = true;
    try {
        const result = await modRequest({action, plan: modPlanId});
        modStatus.textContent = result.message;
        if (action === 'install') modBusy = true;
        if (action === 'vanilla') loadInventory();
        byId('server-mods').scrollIntoView({block: 'nearest'});
    } catch (error) {
        modStatus.textContent = error.message || 'Could not change the mod set.';
    }
    updateModStatus();
}

byId('mod-review-form').addEventListener('submit', event => {
    event.preventDefault();
    reviewModPlan();
});
byId('mod-item').addEventListener('input', resetModPlan);
installMods.addEventListener('click', () => changeModSet('install'));
vanillaMods.addEventListener('click', () => changeModSet('vanilla'));
updateModStatus();

function renderInventory() {
    if (!inventory) return;
    const filter = byId('mod-filter').value.trim().toLowerCase();
    const mods = inventory.mods.filter(mod => [mod.name, mod.id, ...mod.used_by.map(use => use.mission)]
        .some(value => value.toLowerCase().includes(filter)));
    const rows = mods.map(mod => {
        const row = document.createElement('tr');
        const heading = document.createElement('th');
        heading.scope = 'row';
        heading.textContent = mod.name;
        row.append(heading);
        const role = row.insertCell();
        role.textContent = mod.role;
        const selected = document.createElement('span');
        selected.className = 'meta';
        selected.textContent = mod.selected_version ? `Selected · v${mod.selected_version}` : 'Outside selected set';
        role.append(selected);
        if (mod.supports.length) {
            const supports = document.createElement('span');
            supports.className = 'meta';
            supports.textContent = `Supports ${mod.supports.join(', ')}`;
            role.append(supports);
        }
        const usage = row.insertCell();
        const list = document.createElement('ul');
        list.className = 'mod-list';
        for (const use of mod.used_by) {
            const item = document.createElement('li');
            item.textContent = use.mission;
            const tags = document.createElement('span');
            tags.className = 'meta';
            tags.textContent = [use.source, use.kind, use.rotation ? 'In rotation' : 'Outside rotation'].join(' · ');
            item.append(tags);
            list.append(item);
        }
        usage.append(list);
        return row;
    });
    byId('inventory-rows').replaceChildren(...rows);
    byId('inventory-table').hidden = !rows.length;
    const status = mods.length ? `${mods.length} ${mods.length === 1 ? 'package' : 'packages'} · ${inventory.missions.length} missions and scenarios checked.`
        : filter ? 'No packages match this filter.' : 'No mod requirements are recorded for these missions.';
    byId('inventory-status').textContent = inventory.catalog_available ? status : `${status} The catalog is currently unavailable.`;
}

function setOptions(id, values, prompt) {
    const select = byId(id);
    const previous = select.value;
    select.replaceChildren(new Option(prompt, ''), ...values);
    select.value = values.some(option => option.value === previous) ? previous : '';
}

function renderLocalRequirements() {
    const name = byId('local-mod-mission').value;
    const packages = new Map((inventory?.packages || []).map(pkg => [pkg.id, pkg.name]));
    const requirements = inventory?.declarations[name];
    const rows = (Array.isArray(requirements) ? requirements : []).map(key => {
        const item = document.createElement('li');
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.textContent = 'Remove';
        remove.setAttribute('aria-label', `Remove requirement ${packages.get(key) || key}`);
        remove.addEventListener('click', () => changeLocalRequirement('remove', name, key));
        item.append(packages.get(key) || key, remove);
        return item;
    });
    byId('local-mod-list').replaceChildren(...rows);
    byId('add-local-mod').disabled = !name || !byId('local-mod-package').value;
}

async function loadInventory() {
    const sequence = ++inventoryRequest;
    const refresh = byId('refresh-inventory');
    refresh.disabled = true;
    try {
        const response = await panelFetch(modPage.dataset.inventory);
        const result = await response.json();
        if (sequence !== inventoryRequest) return;
        if (!response.ok) throw new Error(result.message);
        inventory = result;
        renderInventory();
        byId('coverage-list').replaceChildren(...result.missions.map(mission => {
            const row = document.createElement('li');
            row.textContent = `${mission.title} · ${mission.source} · ${mission.notes}`;
            return row;
        }));
        byId('mod-coverage').hidden = !result.missions.length;
        byId('workshop-install-options').replaceChildren(...result.missions
            .filter(mission => mission.source === 'Workshop').map(mission => new Option(mission.title, mission.key)));
        setOptions('local-mod-mission', result.local.map(name => new Option(name, name)), 'Choose a scenario');
        setOptions('local-mod-package', result.packages.map(pkg => new Option(`${pkg.name} (${pkg.id})`, pkg.id)), 'Choose a package');
        renderLocalRequirements();
    } catch (error) {
        if (sequence === inventoryRequest) byId('inventory-status').textContent = error.message || 'Could not read the inventory.';
    } finally {
        if (sequence === inventoryRequest) refresh.disabled = false;
    }
}

async function changeLocalRequirement(action, mission, pkg) {
    const form = byId('local-mod-form');
    const controls = [...form.elements, ...byId('local-mod-list').querySelectorAll('button')];
    controls.forEach(control => { control.disabled = true; });
    const status = byId('local-mod-status');
    try {
        const response = await panelFetch(modPage.dataset.local, {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action, mission, package: pkg}),
        });
        const result = await response.json();
        status.textContent = result.message;
        if (response.ok) await loadInventory();
    } catch {
        status.textContent = 'Could not save the local requirement.';
    } finally {
        controls.forEach(control => { control.disabled = false; });
        renderLocalRequirements();
    }
}

byId('refresh-inventory').addEventListener('click', loadInventory);
byId('mod-filter').addEventListener('input', renderInventory);
byId('local-mod-mission').addEventListener('change', renderLocalRequirements);
byId('local-mod-package').addEventListener('change', renderLocalRequirements);
byId('local-mod-form').addEventListener('submit', event => {
    event.preventDefault();
    changeLocalRequirement('add', byId('local-mod-mission').value, byId('local-mod-package').value);
});
loadInventory();

