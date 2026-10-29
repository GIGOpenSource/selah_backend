# Selah

Open-source Selah / Manna stack — Django API + built H5 pages (admin / Selah / Manna).

## What’s included

| Path | Description |
|------|-------------|
| `backend/` | Django API, Docker Compose, nginx sample, `.env.example` |
| `page/` | Built H5: `selah/`, `manna/`, `admin/`, plus `static_*` assets |
| `DEPLOY.md` | Self-host / Docker deployment guide |

Internal对接 docs、TLS 证书、真实 `.env` **未**收录。

## Quick start (Docker)

```bash
cd backend
cp .env.example .env   # 填好 DB / Redis / SECRET
docker compose up -d --build
docker compose exec web python manage.py migrate
```

浏览器打开 `http://localhost/`（Selah H5）、`http://localhost/admin/`（运营台）；`/api/` 走后端。

## Quick start (API only)

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

静态页可用任意静态服务器指向 `page/selah` 或 `page/manna`，并把 API 代理到后端。

API overview: `backend/API.md`  
Dev conventions: `backend/README.md`

## License / use

Replace placeholder package names, OAuth / IAP IDs, and storage buckets in `.env` before production.
