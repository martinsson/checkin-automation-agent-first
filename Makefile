.PHONY: install test run-web run-daemon deploy

# Hetzner server — configure 'hetzner' alias in ~/.ssh/config, or override:
#   make deploy SERVER=root@1.2.3.4
SERVER    ?= hetzner
REMOTE_DIR := /opt/checkin-automation-agent-first

install:
	pip install -r requirements.txt

test:
	pytest

run-web:
	uvicorn src.web.app:app --host 0.0.0.0 --port 8001 --reload

run-daemon:
	python scripts/run.py

# ---------------------------------------------------------------------------
# Build & Deploy to Hetzner
#
# Workflow:
#   make deploy   — rsync source to server, build there, restart services
#
# First-time server setup (manual, once):
#   ssh $(SERVER) 'mkdir -p $(REMOTE_DIR)'
#   scp .env $(SERVER):$(REMOTE_DIR)/.env
#
# The .env file is NEVER baked into the Docker image (.dockerignore excludes it).
# It lives only on the server and is read by docker-compose at container startup.
# To update secrets: scp .env $(SERVER):$(REMOTE_DIR)/.env && make deploy
# ---------------------------------------------------------------------------

deploy:
	@echo "→ Syncing source to $(SERVER):$(REMOTE_DIR)/"
	rsync -av --exclude='.env' --exclude='data/' --exclude='venv/' \
	    --exclude='__pycache__' --exclude='.git/' \
	    ./ $(SERVER):$(REMOTE_DIR)/
	@echo "→ Building images on server"
	ssh $(SERVER) 'cd $(REMOTE_DIR) && docker compose build'
	@echo "→ Restarting services"
	ssh $(SERVER) 'cd $(REMOTE_DIR) && docker compose up -d'
	@echo "→ Service status"
	ssh $(SERVER) 'cd $(REMOTE_DIR) && docker compose ps'
