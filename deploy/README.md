# Deploying the docatho backend (EC2 + gunicorn + nginx + celery)

CI (`.github/workflows/ci.yml`) runs pre-commit + pytest on every push/PR to
`main`. **CD** (`.github/workflows/deploy.yml`) then SSHes into the EC2 host and
runs `deploy/deploy.sh` — but only after CI passes.

```
push to main ──▶ CI (lint + pytest) ──success──▶ Deploy (ssh → deploy.sh)
                                                     │
                            git reset · uv sync · migrate · collectstatic
                                                     │
                          restart gunicorn + celery + celery-beat
```

## 1. GitHub secrets (Settings → Secrets and variables → Actions)

| Secret | Value |
|---|---|
| `EC2_HOST` | EC2 public IP / DNS |
| `EC2_USER` | SSH user (e.g. `ubuntu` or `docatho`) |
| `EC2_SSH_KEY` | **private** key (PEM) whose public key is in the user's `~/.ssh/authorized_keys` |
| `EC2_SSH_PORT` | optional, defaults to 22 |

## 2. One-time server bootstrap (Ubuntu 22.04/24.04 EC2)

```bash
# System packages
sudo apt update
sudo apt install -y nginx postgresql redis-server git curl
# uv (as the deploy user)
curl -LsSf https://astral.sh/uv/install.sh | sh

# App user + directories
sudo useradd --system --create-home --shell /bin/bash docatho
sudo mkdir -p /opt/docatho /etc/docatho /var/www/html
sudo chown -R docatho:www-data /opt/docatho

# Clone the repo
sudo -u docatho git clone https://github.com/<org>/docatho-backend.git /opt/docatho/docatho-backend
cd /opt/docatho/docatho-backend
sudo -u docatho uv sync --locked

# Environment file (fill in real values)
sudo cp deploy/docatho.env.example /etc/docatho/docatho.env
sudo chmod 600 /etc/docatho/docatho.env
sudo nano /etc/docatho/docatho.env

# Postgres DB + user (match POSTGRES_* in the env file)
sudo -u postgres psql -c "CREATE USER docatho WITH PASSWORD 'CHANGE_ME';"
sudo -u postgres psql -c "CREATE DATABASE docatho OWNER docatho;"

# First migrate + collectstatic + create admin
cd /opt/docatho/docatho-backend
set -a; source /etc/docatho/docatho.env; set +a
sudo -u docatho -E .venv/bin/python manage.py migrate
sudo -u docatho -E .venv/bin/python manage.py collectstatic --noinput
sudo -u docatho -E .venv/bin/python manage.py createsuperuser
```

### systemd services

```bash
sudo cp deploy/systemd/docatho-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now docatho-gunicorn docatho-celery docatho-celery-beat
```

### nginx + TLS

```bash
sudo cp deploy/nginx/docatho.conf /etc/nginx/sites-available/docatho
sudo ln -s /etc/nginx/sites-available/docatho /etc/nginx/sites-enabled/docatho
sudo rm -f /etc/nginx/sites-enabled/default
# edit server_name in the conf, then:
sudo nginx -t && sudo systemctl reload nginx
# TLS (adds the :443 server + 80->443 redirect automatically):
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d api.example.com
```

### Passwordless restart for the deploy user

`deploy.sh` runs `sudo systemctl restart docatho-*`. Allow just those:

```bash
echo 'docatho ALL=(root) NOPASSWD: /usr/bin/systemctl restart docatho-gunicorn.service, /usr/bin/systemctl restart docatho-celery.service, /usr/bin/systemctl restart docatho-celery-beat.service' | sudo tee /etc/sudoers.d/docatho-deploy
sudo chmod 440 /etc/sudoers.d/docatho-deploy
```

> If `EC2_USER` isn't `docatho`, make sure that user can `cd` into the repo,
> run `uv`, read `/etc/docatho/docatho.env`, and has the sudoers rule above.

## 3. After bootstrap

Every push to `main` that passes CI auto-deploys. To deploy manually, use the
**Actions → Deploy → Run workflow** button, or on the box:
`bash /opt/docatho/docatho-backend/deploy/deploy.sh`.

## Notes
- Static & media are stored on **S3** (`django-storages`); nginx does not serve
  them, so the `DJANGO_AWS_*` vars are required for `collectstatic` to work.
- `celery` + `django-celery-beat` were added to the project; the worker and beat
  run as their own systemd units and use Redis as the broker (`REDIS_URL`).
- Production forces HTTPS (`DJANGO_SECURE_SSL_REDIRECT=True`). Provision TLS with
  certbot before sending real traffic, or temporarily set it to `False`.
