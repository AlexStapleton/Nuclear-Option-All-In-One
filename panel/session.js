async function panelFetch(url, options = {}) {
    const headers = new Headers(options.headers);
    if (!['GET', 'HEAD', 'OPTIONS'].includes((options.method || 'GET').toUpperCase())) {
        headers.set('X-CSRF-Token', document.querySelector('meta[name="panel-csrf"]').content);
    }
    const response = await fetch(url, {...options, headers});
    if (response.status === 401) document.getElementById('session-expired').hidden = false;
    return response;
}
