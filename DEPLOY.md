# Deploy Selah backend

## Prerequisites

- Docker + Docker Compose v2
- Or: Python 3.11+, PostgreSQL 14+, Redis 6+

## 1. Configure environment

```bash
cd backend
cp .env.example .env
```

Edit `.env` at least:

- `POSTGRES_*` / `REDIS_PASSWORD`
- `DJANGO_SECRET_KEY`
- OAuth / IAP / storage keys only if you need those features

Do **not** commit `.env`, certs, or service-account JSON.

## 2. Docker Compose

```bash
cd backend
docker compose up -d --build
```

Default layout:

| Service | Role |
|---------|------|
| `db` | PostgreSQL |
| `redis` | Redis (password from `.env`) |
| `web` | Gunicorn + Django |
| `nginx` | HTTP：H5（`../page`）+ `/api` 反代，端口 80 |

Compose 会把仓库根下的 `page/` 只读挂载到 nginx 的 `/app/page`。

Migrations (first boot or after pull):

```bash
docker compose exec web python manage.py migrate
docker compose exec web python manage.py collectstatic --noinput
```

Optional HTTPS: uncomment TLS ports/volumes in `docker-compose.yml` and the HTTPS `server` block in `nginx/conf.d/default.conf`, then mount your own certs (not shipped).

## 3. Without Docker

1. Create DB/user matching `.env`
2. Start Redis with the same password
3. `pip install -r requirements.txt`
4. `python manage.py migrate && python manage.py runserver`

For production, put Gunicorn behind nginx or another reverse proxy; set `DJANGO_SETTINGS_MODULE` to your production settings module and `DEBUG=False`.

## 4. Hardening checklist

- [ ] Unique strong `DJANGO_SECRET_KEY`, DB and Redis passwords  
- [ ] `ALLOWED_HOSTS` limited to your domains  
- [ ] `ALLOW_OAUTH_UNVERIFIED=0` and `ALLOW_IAP_UNVERIFIED=0` in production  
- [ ] GCS / COS credentials via env or a host path **outside** git  
- [ ] TLS terminated at nginx or a load balancer  

## 5. Useful commands

```bash
docker compose logs -f web
docker compose exec web python manage.py createsuperuser
docker compose down
```
