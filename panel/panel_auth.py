"""Form sign-in and session protection for the administration panel."""

import re
from datetime import timedelta
from hmac import compare_digest, digest
from pathlib import Path
from secrets import token_bytes, token_urlsafe
from tempfile import NamedTemporaryFile
from threading import Lock

from flask import flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import config

PANEL_PAGES = ("index", "settings_page", "moderation_page", "logs_page", "mods_page", "reports_page", "change_password")
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
PASSWORD_HASH_METHOD = "scrypt:32768:8:1"
PASSWORD_HASH = re.compile(r"scrypt:32768:8:1\$[A-Za-z0-9]{16}\$[a-f0-9]{128}")
PASSWORD_READ_ERROR = "Could not read the panel password. Check the server data folder permissions."


def csrf_token():
    token = session.get("csrf_token")
    if token is None:
        token = session["csrf_token"] = token_urlsafe(32)
    return token


def valid_csrf():
    expected = session.get("csrf_token")
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
    return expected is not None and compare_digest(expected.encode(), supplied.encode())


def read_password_hash(path):
    if path.is_symlink():
        raise ValueError("The panel password file must be a regular file.")
    try:
        with path.open(encoding="ascii") as stream:
            value = stream.read(256).strip()
    except FileNotFoundError:
        return None
    if not PASSWORD_HASH.fullmatch(value):
        raise ValueError("Invalid panel password file.")
    return value


def password_matches(password, stored_hash):
    if stored_hash is None:
        return compare_digest(password.encode(), config.PASSWORD.encode())
    return len(password) <= 256 and check_password_hash(stored_hash, password)


def write_password_hash(path, password):
    password_hash = generate_password_hash(password, method=PASSWORD_HASH_METHOD, salt_length=16)
    stream = NamedTemporaryFile(mode="w", encoding="ascii", dir=path.parent, delete=False)
    temporary = Path(stream.name)
    try:
        with stream:
            stream.write(password_hash + "\n")
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def install_auth(app, data_dir):
    password_lock = Lock()
    app.config.update(
        SECRET_KEY=token_bytes(32),
        PANEL_PASSWORD_FILE=data_dir / ".panel-password",
        SESSION_COOKIE_NAME="nuclear_option_panel",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(config.SSL_CERT_PATH and config.SSL_KEY_PATH),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        SESSION_REFRESH_EACH_REQUEST=False,
    )
    app.jinja_env.globals["csrf_token"] = csrf_token

    def password_version(stored_hash):
        value = (stored_hash or "bootstrap").encode("ascii")
        return digest(app.secret_key, value, "sha256").hex()

    def signed_in(stored_hash):
        return session.get("password_version") == password_version(stored_hash)

    # The pinned upstream command views have a single HTTP Basic decorator.
    # Replace that gate with the session check below while retaining the handlers.
    for rule in app.url_map.iter_rules():
        if rule.rule.startswith("/command/"):
            app.view_functions[rule.endpoint] = app.view_functions[rule.endpoint].__wrapped__

    @app.before_request
    def protect_panel():
        if request.endpoint in (None, "login", "static"):
            return None
        try:
            valid = session.get("password_version") and signed_in(read_password_hash(app.config["PANEL_PASSWORD_FILE"]))
        except (OSError, ValueError):
            return jsonify(message=PASSWORD_READ_ERROR), 503
        if not valid:
            if session.get("password_version"):
                session.clear()
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
        try:
            stored_hash = read_password_hash(app.config["PANEL_PASSWORD_FILE"])
        except (OSError, ValueError):
            return render_template("login.html", next_page=target, error=PASSWORD_READ_ERROR, username=config.USERNAME), 503
        if signed_in(stored_hash):
            return redirect(target)
        error = None
        status = 200
        if request.method == "POST":
            if not valid_csrf():
                error = "This sign-in form has expired. Please try again."
                status = 403
            elif (compare_digest(request.form.get("username", "").encode(), config.USERNAME.encode())
                  and password_matches(request.form.get("password", ""), stored_hash)):
                session.clear()
                session.permanent = True
                session["password_version"] = password_version(stored_hash)
                csrf_token()
                return redirect(target, code=303)
            else:
                error = "The username or password is incorrect."
                status = 401
        return render_template("login.html", next_page=target, error=error, username=config.USERNAME,
                               bootstrap=stored_hash is None), status

    @app.route("/account/password", methods=["GET", "POST"])
    def change_password():
        error = None
        status = 200
        if request.method == "POST":
            current = request.form.get("current_password", "")
            new = request.form.get("new_password", "")
            confirmation = request.form.get("confirm_password", "")
            if not 12 <= len(new) <= 256 or not new.strip() or any(char in new for char in "\0\r\n"):
                error = "Enter a new password with 12 to 256 characters on one line."
            elif new != confirmation:
                error = "The new passwords do not match."
            else:
                path = app.config["PANEL_PASSWORD_FILE"]
                with password_lock:
                    try:
                        stored_hash = read_password_hash(path)
                        if not signed_in(stored_hash) or not password_matches(current, stored_hash):
                            error = "The current password is incorrect or has changed."
                        elif compare_digest(current.encode(), new.encode()):
                            error = "Choose a password different from your current password."
                        else:
                            write_password_hash(path, new)
                    except (OSError, ValueError):
                        error = "Could not save the panel password. Check the server data folder permissions."
                        status = 503
                if error is None:
                    session.clear()
                    flash("Panel password changed. Sign in with your new password.")
                    return redirect(url_for("login"), code=303)
            if status == 200:
                status = 400
        return render_template("password.html", error=error), status

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"), code=303)
