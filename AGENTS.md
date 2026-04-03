# Repository Guidelines

## Project Structure & Module Organization
This repository is split into three services:

- `backend/`: Flask API, SQLAlchemy models, Celery tasks, Alembic migrations, and backend tests in `backend/tests/`.
- `inference/`: separate Flask-based inference services and tests in `inference/tests/`.
- `frontend/`: Vite + React + TypeScript UI, with pages under `frontend/src/pages/`, shared UI in `frontend/src/components/`, and shared state/context in `frontend/src/contexts/`.

Top-level Docker and orchestration files (`docker-compose*.yml`, `nginx/`, `go2rtc.yaml`) define local and production environments. Keep service-specific changes inside the matching directory.

## Build, Test, and Development Commands
Use the Makefile for common workflows:

- `make dev` or `make dev-backend`: start Postgres/Redis, then run the backend locally.
- `make dev-inference`: start inference services from `docker-compose.inference.yml`.
- `make frontend-dev`: run the Vite dev server.
- `make up` / `make down`: bring the full Docker stack up or down.
- `make build` / `make build-inference`: rebuild container images.
- `make migrate`: apply database bootstrap/migrations inside the API container.

Direct service commands are also used in CI:

- `cd backend && python -m pytest tests/ -v --tb=short --cov=app --cov-report=xml`
- `cd inference && python -m pytest tests/ -v --tb=short`
- `cd frontend && npm run lint && npm run build`

## Coding Style & Naming Conventions
Python uses 4-space indentation, `snake_case` module names, and Flake8 checks with `--max-line-length=200 --ignore=E402,W503`. Frontend code uses TypeScript, 2-space indentation, `PascalCase` component/page files such as `DashboardPage.tsx`, and `camelCase` for hooks/helpers. Prefer small service modules under `app/services/` and keep API routes in `app/api/`.

## Testing Guidelines
Place backend and inference tests beside their services in `backend/tests/` and `inference/tests/`. Follow the existing `test_*.py` naming pattern, for example `test_model_management.py`. Cover API behavior, service logic, and regression paths when changing recognition, embeddings, or ingestion flows. Backend CI collects coverage for `backend/app`; keep new backend code exercised by tests before opening a PR.

## Commit & Pull Request Guidelines
Recent history favors short, imperative commit subjects like `Add threshold filtering for retrieval recall`. Follow that pattern and avoid placeholder messages such as `1`. PRs should describe the affected area (`backend`, `frontend`, or `inference`), list verification commands run, note any schema or env changes, and include screenshots for visible frontend changes.

## Security & Configuration Tips
Start from `.env.example`; do not commit real secrets, API keys, or camera credentials. Rotate default credentials before deployment, especially `SECRET_KEY`, database passwords, and `INFERENCE_API_TOKEN`.
