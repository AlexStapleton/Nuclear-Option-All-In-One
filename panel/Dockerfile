FROM python:3.12-slim-bookworm

ARG PANEL_REV=1d3aba8af001abe2a380b3518753e0d1aef397a1
ADD https://github.com/Shockfront-Studios/Nuclear-Option-Server-Tools/archive/${PANEL_REV}.tar.gz /tmp/panel.tar.gz
RUN tar -xzf /tmp/panel.tar.gz -C /tmp \
    && mv /tmp/Nuclear-Option-Server-Tools-${PANEL_REV}/ServerControlPanel /app \
    && rm -rf /tmp/panel.tar.gz /tmp/Nuclear-Option-Server-Tools-${PANEL_REV} \
    && python -m pip install --no-cache-dir -r /app/requirements.txt

WORKDIR /app
COPY config.py /app/config.py
COPY manage.py mission_import.py /app/
COPY settings.html /app/templates/settings.html
COPY dashboard.html logs.html moderation.html /app/templates/
COPY navigation.html /app/templates/navigation.html
COPY dashboard.css dashboard.js logs.js moderation.js mission-import.js /app/static/
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
CMD ["python", "manage.py"]
