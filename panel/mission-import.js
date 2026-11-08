const importForm = document.getElementById('mission-import');
const copyForm = document.getElementById('mission-copy');
const fileInput = document.getElementById('mission-upload');
const copyName = document.getElementById('copy-name');
const importStatus = document.getElementById('import-status');
const useMission = document.getElementById('use-saved-mission');
const missionFields = [...copyForm.querySelectorAll('[data-mission-field]')];
let original = {};

async function uploadMission(button, form, onSuccess) {
    if (fileInput.disabled) return;
    const data = new FormData(importForm);
    if (form === copyForm) {
        const edits = {};
        for (const input of missionFields) {
            const key = input.dataset.missionField;
            if (!(key in original)) continue;
            const value = input.type === 'checkbox' ? input.checked
                : input.type === 'number' ? input.valueAsNumber : input.value;
            if (value !== original[key]) edits[key] = value;
        }
        data.set('name', copyName.value.trim());
        data.set('edits', JSON.stringify(edits));
    }
    fileInput.disabled = button.disabled = true;
    importStatus.dataset.state = '';
    importStatus.textContent = form === copyForm ? 'Saving the mission…' : 'Reading the mission…';
    try {
        const response = await fetch(importForm.dataset.url, {
            method: 'POST', headers: {'X-Mission-Upload': '1'}, body: data,
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.message);
        onSuccess(result);
        importStatus.dataset.state = 'success';
    } catch (error) {
        importStatus.dataset.state = 'error';
        importStatus.textContent = error instanceof SyntaxError || !error.message
            ? 'The panel could not read the mission upload.' : error.message;
    } finally {
        fileInput.disabled = button.disabled = false;
    }
}

fileInput.addEventListener('change', () => {
    copyForm.hidden = useMission.hidden = true;
    importStatus.textContent = '';
});

importForm.addEventListener('submit', (event) => {
    event.preventDefault();
    uploadMission(event.submitter, importForm, ({name, fields}) => {
        original = fields;
        copyName.value = `${name} Copy`;
        for (const input of missionFields) {
            const key = input.dataset.missionField;
            const available = Object.hasOwn(fields, key);
            input.closest('label').hidden = input.disabled = !available;
            if (!available) continue;
            if (input.type === 'checkbox') input.checked = fields[key];
            else input.value = fields[key];
        }
        copyForm.hidden = false;
        useMission.hidden = true;
        importStatus.textContent = 'Review the editable fields and choose a new mission name.';
        copyName.focus();
    });
});

copyForm.addEventListener('submit', (event) => {
    event.preventDefault();
    uploadMission(event.submitter, copyForm, ({name, message}) => {
        const preset = document.getElementById('preset');
        const local = document.getElementById('local-missions');
        const option = new Option(name, name);
        option.dataset.group = 'User';
        local.append(option);
        local.hidden = false;
        option.selected = true;
        preset.dispatchEvent(new Event('change'));
        useMission.hidden = false;
        importStatus.textContent = message;
    });
});
