.PHONY: dev up down build migrate seed logs clean frontend-dev

# ─── Development ─────────────────────────────────────────────────────────────
dev:
	docker-compose up -d postgres redis
	cd backend && FLASK_ENV=development python wsgi.py

up:
	docker-compose up -d

down:
	docker-compose down

build:
	docker-compose build

# ─── Database ─────────────────────────────────────────────────────────────────
migrate:
	docker-compose exec flask-api flask db upgrade

migrate-init:
	docker-compose exec flask-api flask db init
	docker-compose exec flask-api flask db migrate -m "initial schema"
	docker-compose exec flask-api flask db upgrade

seed:
	docker-compose exec flask-api flask seed-db

# ─── Frontend ─────────────────────────────────────────────────────────────────
frontend-dev:
	cd frontend && npm run dev

frontend-build:
	cd frontend && npm run build

frontend-install:
	cd frontend && npm ci

# ─── Logs ─────────────────────────────────────────────────────────────────────
logs:
	docker-compose logs -f

logs-api:
	docker-compose logs -f flask-api

logs-worker:
	docker-compose logs -f celery-worker

# ─── Production ───────────────────────────────────────────────────────────────
prod-up:
	docker-compose -f docker-compose.prod.yml up -d

prod-down:
	docker-compose -f docker-compose.prod.yml down

prod-migrate:
	docker-compose -f docker-compose.prod.yml exec flask-api flask db upgrade

# ─── Utilities ────────────────────────────────────────────────────────────────
clean:
	docker-compose down -v
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null; true
	find . -name "*.pyc" -delete 2>/dev/null; true

shell:
	docker-compose exec flask-api python
