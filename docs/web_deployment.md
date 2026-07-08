# Web Deployment

## Environment

Create `.env` from `.env.example` and configure model credentials plus Web login:

```bash
export WEB_USERNAME=admin
export WEB_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export WEB_PASSWORD_HASH="$(python -c 'from webapp.auth import make_password_hash; print(make_password_hash("change-me"))')"
export WEB_MAX_UPLOAD_SIZE=100MB
```

`WEB_PASSWORD_HASH` supports the built-in `pbkdf2_sha256$iterations$salt$hash` format. A plain SHA-256 hex digest is also accepted for migration only.

## Run Manually

```bash
python -m pip install -e .
uvicorn webapp.app:app --host 0.0.0.0 --port 8000
```

Open `http://SERVER_IP:8000/login`.

## systemd Example

```ini
[Unit]
Description=Construction Schedule Web
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/autogen-construction-schedule-system
EnvironmentFile=/opt/autogen-construction-schedule-system/.env
ExecStart=/opt/autogen-construction-schedule-system/.venv/bin/uvicorn webapp.app:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Tasks are stored under `data/web/jobs/{job_id}/`. Restarting the service keeps historical job metadata and results because no database is required in the first version.
