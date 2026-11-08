"""Form sign-in and session protection for the administration panel."""

from datetime import timedelta
from hmac import compare_digest
from secrets import token_bytes, token_urlsafe

from flask import jsonify, redirect, render_template, request, session, url_for

import config

PANEL_PAGES = ("index", "settings_page", "moderation_page", "logs_page", "mods_page")
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def csrf_token():
    token = session.get("csrf_token")
    if token is None:
        token = session["csrf_token"] = token_urlsafe(32)
    return token


def valid_csrf():
    expected = session.get("csrf_token")
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
    return expected is not None and compare_digest(expected.encode(), supplied.encode())


def credentials_match(username, password):
    user_ok = compare_digest(username.encode(), config.USERNAME.encode())
    password_ok = compare_digest(password.encode(), config.PASSWORD.encode())
    return user_ok and password_ok


def install_auth(app):
    app.config.update(
        SECRET_KEY=token_bytes(32),
        SESSION_COOKIE_NAME="nuclear_option_panel",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(config.SSL_CERT_PATH and config.SSL_KEY_PATH),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        SESSION_REFRESH_EACH_REQUEST=False,
    )
    app.jinja_env.globals["csrf_token"] = csrf_token

    # The pinned upstream command views have a single HTTP Basic decorator.
    # Replace that gate with the session check below while retaining the handlers.
    for rule in app.url_map.iter_rules():
        if rule.rule.startswith("/command/"):
            app.view_functions[rule.endpoint] = app.view_functions[rule.endpoint].__wrapped__

    @app.before_request
    def protect_panel():
        if request.endpoint in (None, "login", "static"):
            return None
        if not session.get("signed_in"):
            if request.endpoint in PANEL_PAGES and request.method in SAFE_METHODS:
                return redirect(url_for("login", next=request.path))
            message = "Your panel session has expired. Sign in again."
            return jsonify(message=message, error=message), 401
        if request.method not in SAFE_METHODS and not valid_csrf():
            message = "This page is out of date. Refresh it and try again."
            return jsonify(message=message, error=message), 403
        return None

    @app.after_request
    def prevent_caching(response):
        if request.endpoint and request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/login", methods=["GET", "POST"])
    def login():
        target = request.form.get("next") if request.method == "POST" else request.args.get("next")
        if target not in {url_for(endpoint) for endpoint in PANEL_PAGES}:
            target = url_for("index")
        if session.get("signed_in"):
            return redirect(target)
        error = None
        status = 200
        if request.method == "POST":
            if not valid_csrf():
                error = "This sign-in form has expired. Please try again."
                status = 403
            elif credentials_match(request.form.get("username", ""), request.form.get("password", "")):
                session.clear()
                session.permanent = True
                session["signed_in"] = True
                csrf_token()
                return redirect(target, code=303)
            else:
                error = "The username or password is incorrect."
                status = 401
        return render_template("login.html", next_page=target, error=error, username=config.USERNAME), status

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"), code=303)
