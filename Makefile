.PHONY: dev dev-backend dev-inference up down build build-inference migrate seed logs logs-api logs-worker logs-inference clean frontend-dev

# ─── Development ─────────────────────────────────────────────────────────────
dev:
	docker-compose up -d postgres redis
	cd backend && FLASK_ENV=development python3 wsgi.py

dev-backend:
	docker-compose up -d postgres redis
	cd backend && FLASK_ENV=development python3 wsgi.py

dev-inference:
	docker-compose -f docker-compose.inference.yml up -d detector-api retrieval-api

up:
	docker-compose up -d

down:
	docker-compose down

build:
	docker-compose build

build-inference:
	docker-compose -f docker-compose.inference.yml build

# ─── Database ─────────────────────────────────────────────────────────────────
migrate:
	docker-compose exec flask-api flask bootstrap-db

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

logs-inference:
	docker-compose -f docker-compose.inference.yml logs -f detector-api retrieval-api

# ─── Production ───────────────────────────────────────────────────────────────
prod-up:
	docker-compose -f docker-compose.prod.yml up -d

prod-down:
	docker-compose -f docker-compose.prod.yml down

prod-migrate:
	docker-compose -f docker-compose.prod.yml exec flask-api flask bootstrap-db

# ─── Utilities ────────────────────────────────────────────────────────────────
clean:
	docker-compose down -v
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null; true
	find . -name "*.pyc" -delete 2>/dev/null; true

shell:
	docker-compose exec flask-api python
