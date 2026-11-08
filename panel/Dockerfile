FROM python:3.12-slim-bookworm

ARG PANEL_REV=1d3aba8af001abe2a380b3518753e0d1aef397a1
ADD https://github.com/Shockfront-Studios/Nuclear-Option-Server-Tools/archive/${PANEL_REV}.tar.gz /tmp/panel.tar.gz
RUN tar -xzf /tmp/panel.tar.gz -C /tmp \
    && mv /tmp/Nuclear-Option-Server-Tools-${PANEL_REV}/ServerControlPanel /app \
    && rm -rf /tmp/panel.tar.gz /tmp/Nuclear-Option-Server-Tools-${PANEL_REV} \
    && python -m pip install --no-cache-dir -r /app/requirements.txt

WORKDIR /app
COPY config.py /app/config.py
COPY manage.py /app/manage.py
COPY settings.html /app/templates/settings.html
COPY dashboard.html /app/templates/dashboard.html
COPY dashboard.css dashboard.js /app/static/
RUN sed -i '/<h1 class="mb-4">Nuclear Option Server Manager<\/h1>/a\        <p><a href="/" class="btn btn-secondary">Dashboard</a> <a href="/settings" class="btn btn-primary">Server Settings \&amp; Missions</a></p>' templates/index.html \
    && grep -q 'href="/settings"' templates/index.html
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
CMD ["python", "manage.py"]
