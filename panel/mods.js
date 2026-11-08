const byId = id => document.getElementById(id);
const reviewMods = byId('review-mods');
const installMods = byId('install-mods');
const saveModSet = byId('save-mod-set');
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
let savedSetsSignature;
const modPage = byId('mod-management');

function resetModPlan() {
    modReviewRequest += 1;
    modPlanId = undefined;
    modPlanBox.hidden = true;
    installMods.disabled = saveModSet.disabled = true;
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
        const {selected, selected_id: selectedId, sets, running, message} = await modRequest();
        const completed = modBusy && !running;
        byId('selected-mods').textContent = selected ? `Server mod set: ${selected}` : 'No mods enabled by the panel.';
        vanillaMods.hidden = !selected;
        vanillaMods.disabled = running;
        modBusy = running;
        installMods.disabled = saveModSet.disabled = running || !modPlanId;
        showSavedSets(sets, selectedId, running);
        if (message) modStatus.textContent = message;
        if (running) modPoll = setTimeout(updateModStatus, 2500);
        if (completed) loadInventory();
    } catch (error) {
        byId('selected-mods').textContent = error.message || 'Could not check the selected mod set.';
        if (modBusy) modPoll = setTimeout(updateModStatus, 5000);
    }
}

async function reviewModPlan(overrides = {}, update = null) {
    const target = byId('mod-item').value.trim();
    if (!target && !update) return;
    const request = ++modReviewRequest;
    const problems = byId('workshop-mod-problems');
    reviewMods.disabled = installMods.disabled = saveModSet.disabled = true;
    modPlanId = undefined;
    modPlanBox.hidden = false;
    const heading = byId('mod-plan-heading');
    heading.textContent = update ? `Review ${update.exact ? 'version change' : 'update'} for ${update.name}` : 'Review install';
    if (update) heading.focus();
    installMods.textContent = update ? (update.exact ? 'Apply version and restart' : 'Apply update and restart') : 'Install and restart server';
    problems.textContent = update ? 'Checking the new release and its dependencies…' : 'Building the install plan…';
    byId('workshop-mod-assets').replaceChildren();
    byId('workshop-mod-choices').replaceChildren();
    const requirements = byId('mod-plan-requirements');
    requirements.hidden = true;
    try {
        const plan = await modRequest(update ? {action: update.exact ? 'preview_version' : 'preview_update', package: update.id,
            version: update.exact ? update.version : update.latest_version}
            : {action: 'preview', item: target, overrides});
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
        if (update) {
            for (const change of plan.changes) {
                const row = document.createElement('li');
                const version = change.from ? `v${change.from} → v${change.to}` : `Add v${change.to}`;
                row.textContent = `${change.name} · ${version}${change.requested ? '' : ' · Required dependency'}${change.game_version ? ` · Catalog game version ${change.game_version}` : ''}`;
                const mod = inventory?.mods.find(entry => entry.id === change.id);
                const pins = (mod?.used_by || []).filter(use => use.requested_version && use.requested_version !== change.to);
                if (pins.length) {
                    const note = document.createElement('p');
                    note.className = 'meta';
                    note.textContent = pins.map(use => `${use.mission} lists v${use.requested_version}`).join(' · ');
                    row.append(note);
                }
                byId('workshop-mod-assets').append(row);
            }
            const info = plan.requirements.length
                ? `This release requires ${plan.requirements.map(dep => `${dep.name}${dep.minimum ? ` v${dep.minimum} or newer` : ''} (plan uses v${dep.version})`).join(', ')}.`
                : 'The catalog lists no package dependencies for this release.';
            requirements.textContent = update.exact ? `${info} This version will be pinned in the new set.` : info;
            requirements.hidden = false;
        }
        for (const asset of update ? [] : plan.assets) {
            const row = document.createElement('li');
            row.textContent = `${asset.name} v${asset.version}${asset.game_version ? ` · Catalog game version ${asset.game_version}` : ''}`;
            byId('workshop-mod-assets').append(row);
        }
        if (!plan.problems.length) modPlanId = plan.id;
        installMods.disabled = saveModSet.disabled = modBusy || !modPlanId;
    } catch (error) {
        if (request === modReviewRequest) problems.textContent = error.message || 'Could not build the install plan.';
    } finally {
        if (request === modReviewRequest) reviewMods.disabled = false;
    }
}

async function changeModSet(action, data = {}) {
    installMods.disabled = saveModSet.disabled = vanillaMods.disabled = true;
    try {
        const result = await modRequest({action, plan: modPlanId, ...data});
        modStatus.textContent = result.message;
        if (['install', 'save_set', 'clean_copy'].includes(action)) modBusy = true;
        if (['vanilla', 'activate', 'pin'].includes(action)) {
            resetModPlan();
            loadInventory();
        }
        if (action !== 'pin') byId('server-mods').scrollIntoView({block: 'nearest'});
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
saveModSet.addEventListener('click', () => changeModSet('save_set'));
vanillaMods.addEventListener('click', () => changeModSet('vanilla'));
updateModStatus();

function showSavedSets(sets, selectedId, running) {
    const signature = JSON.stringify([sets, selectedId, running]);
    if (signature === savedSetsSignature) return;
    savedSetsSignature = signature;
    const current = sets.find(set => set.id === selectedId);
    const currentVersions = new Map((current?.versions || []).map(mod => [mod.name, mod.version]));
    const rows = sets.map(set => {
        const details = document.createElement('details');
        details.className = 'saved-mod-set';
        const summary = document.createElement('summary');
        const differences = set.versions.filter(mod => currentVersions.get(mod.name) !== mod.version);
        const title = set.clean_copy ? `${set.title} (clean copy)` : set.title;
        summary.textContent = set.id === selectedId ? `${title} · Selected`
            : `${title} · ${differences.length ? differences.slice(0, 2).map(mod => `${mod.name} v${mod.version}`).join(', ') : 'Saved version set'}`;
        details.append(summary);
        const list = document.createElement('ul');
        list.className = 'mod-list';
        for (const mod of set.versions) {
            const item = document.createElement('li');
            item.textContent = `${mod.name} v${mod.version}${mod.pinned ? ' · Pinned' : ''}`;
            list.append(item);
        }
        details.append(list);
        const actions = document.createElement('div');
        actions.className = 'actions';
        if (set.id !== selectedId) {
            const activate = document.createElement('button');
            activate.type = 'button';
            activate.textContent = 'Use this set and restart';
            activate.disabled = running;
            activate.addEventListener('click', () => changeModSet('activate', {set: set.id}));
            actions.append(activate);
        }
        const clean = document.createElement('button');
        clean.type = 'button';
        clean.textContent = 'Download clean copy';
        clean.disabled = running;
        clean.addEventListener('click', () => changeModSet('clean_copy', {set: set.id}));
        actions.append(clean);
        details.append(actions);
        return details;
    });
    byId('saved-sets').replaceChildren(...rows);
    byId('saved-sets-empty').hidden = Boolean(rows.length);
}

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
        const badge = document.createElement('span');
        badge.className = 'mod-role';
        badge.textContent = mod.role;
        heading.append(badge);
        row.append(heading);
        const versions = row.insertCell();
        const selected = document.createElement('span');
        selected.textContent = mod.selected_version ? `In server set · v${mod.selected_version}`
            : inventory.selected_title ? 'Not enabled by panel' : 'Needed for listed missions';
        versions.append(selected);
        const latest = document.createElement('span');
        latest.className = 'meta';
        latest.textContent = mod.pinned ? `Pinned to v${mod.selected_version} for this set`
            : mod.id === 'panel-bepinex' ? 'Loader version managed by the panel'
            : mod.update_available ? `Update available · v${mod.latest_version}`
                : mod.latest_version ? `Latest supported release · v${mod.latest_version}` : 'Catalog version unavailable';
        versions.append(latest);
        if (mod.update_available) {
            const review = document.createElement('button');
            review.type = 'button';
            review.className = 'mod-review-update';
            review.textContent = 'Review update';
            review.setAttribute('aria-label', `Review update for ${mod.name}`);
            review.addEventListener('click', () => reviewModPlan({}, mod));
            versions.append(review);
        }
        if (mod.selected_version && mod.id !== 'panel-bepinex') {
            const options = document.createElement('details');
            options.className = 'mod-version-options';
            const summary = document.createElement('summary');
            summary.textContent = 'Version options';
            options.append(summary);
            const pin = document.createElement('button');
            pin.type = 'button';
            pin.className = 'mod-review-update';
            pin.textContent = mod.pinned ? 'Remove pin' : 'Pin current version';
            pin.addEventListener('click', () => changeModSet('pin', {set: inventory.selected_id, package: mod.id, pinned: !mod.pinned}));
            options.append(pin);
            const versionsAvailable = mod.versions.filter(version => version !== mod.selected_version);
            if (versionsAvailable.length) {
                const select = document.createElement('select');
                select.setAttribute('aria-label', `Alternate version of ${mod.name}`);
                select.append(new Option('Choose another version', ''), ...versionsAvailable.map(version => new Option(`v${version}`, version)));
                const reviewVersion = document.createElement('button');
                reviewVersion.type = 'button';
                reviewVersion.className = 'mod-review-update';
                reviewVersion.textContent = 'Review this version';
                reviewVersion.disabled = true;
                select.addEventListener('change', () => { reviewVersion.disabled = !select.value; });
                reviewVersion.addEventListener('click', () => reviewModPlan({}, {...mod, version: select.value, exact: true}));
                options.append(select, reviewVersion);
            }
            versions.append(options);
        }
        const usage = row.insertCell();
        const missionCount = mod.used_by.length;
        const first = mod.used_by[0];
        const lead = document.createElement('span');
        lead.textContent = missionCount === 1 ? first.mission : `${missionCount} missions and scenarios`;
        usage.append(lead);
        const details = document.createElement('details');
        const summary = document.createElement('summary');
        summary.textContent = mod.supports.length ? `Details · ${mod.supports.length} dependent mods` : 'Usage details';
        details.append(summary);
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
        details.append(list);
        if (mod.required_by.length) {
            const minimums = document.createElement('ul');
            minimums.className = 'mod-list meta';
            for (const dependency of mod.required_by) {
                const item = document.createElement('li');
                item.textContent = `${dependency.name} v${dependency.version} requires ${dependency.minimum ? `v${dependency.minimum} or newer` : 'this package'}`;
                minimums.append(item);
            }
            details.append(minimums);
        }
        if (mod.supports.length) {
            const supports = document.createElement('p');
            supports.className = 'meta';
            supports.textContent = `Required by ${mod.supports.join(', ')}`;
            details.append(supports);
        }
        usage.append(details);
        return row;
    });
    byId('inventory-rows').replaceChildren(...rows);
    byId('inventory-table').hidden = !rows.length;
    const updates = inventory.mods.filter(mod => mod.update_available).length;
    byId('inventory-set').textContent = inventory.selected_title ? `Server mod set: ${inventory.selected_title}` : 'No mods enabled by the panel';
    const status = mods.length ? `${mods.length} ${mods.length === 1 ? 'package' : 'packages'} shown.`
        : filter ? 'No packages match this filter.' : 'No mod requirements are recorded for these missions.';
    const updateStatus = updates ? ` ${updates} ${updates === 1 ? 'update' : 'updates'} available.`
        : inventory.selected_title ? ' No newer supported releases found for the server set.' : '';
    byId('inventory-status').textContent = inventory.catalog_available ? status + updateStatus : `${status} The catalog is currently unavailable.`;
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

async function loadInventory(checkUpdates = false) {
    const sequence = ++inventoryRequest;
    const refresh = byId('refresh-inventory');
    refresh.disabled = true;
    const checkStatus = byId('update-check-status');
    if (checkUpdates) checkStatus.textContent = 'Checking the NOMM catalog…';
    try {
        const response = await panelFetch(`${modPage.dataset.inventory}${checkUpdates ? '?check=1' : ''}`);
        const result = await response.json();
        if (sequence !== inventoryRequest) return;
        if (!response.ok) throw new Error(result.message);
        inventory = result;
        const conflicts = byId('compatibility-conflicts');
        conflicts.replaceChildren(...result.conflicts.map(message => {
            const item = document.createElement('li');
            item.textContent = message;
            return item;
        }));
        conflicts.hidden = !result.conflicts.length;
        if (checkUpdates) checkStatus.textContent = result.catalog_available
            ? `Last checked at ${new Date().toLocaleTimeString()}.` : 'Update check failed. Try again when the catalog is available.';
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
        if (checkUpdates && sequence === inventoryRequest) checkStatus.textContent = 'Update check failed.';
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

byId('refresh-inventory').addEventListener('click', () => loadInventory(true));
byId('mod-filter').addEventListener('input', renderInventory);
byId('local-mod-mission').addEventListener('change', renderLocalRequirements);
byId('local-mod-package').addEventListener('change', renderLocalRequirements);
byId('local-mod-form').addEventListener('submit', event => {
    event.preventDefault();
    changeLocalRequirement('add', byId('local-mod-mission').value, byId('local-mod-package').value);
});
loadInventory();

