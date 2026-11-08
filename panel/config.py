"""Settings consumed by Shockfront's Server Control Panel."""

import os

SERVER_PORTS = [7779]
FLASK_HOST = "0.0.0.0"
FLASK_PORT = 5000
USERNAME = "admin"
PASSWORD = os.environ["PANEL_PASSWORD"]
if not PASSWORD:
    raise ValueError("Set PANEL_PASSWORD in .panel.env before starting the panel.")

SSL_CERT_PATH = ""
SSL_KEY_PATH = ""
STEAM_API_KEY = os.environ.get("STEAM_API_KEY", "")
