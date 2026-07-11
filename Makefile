.PHONY: install test run-web run-daemon deploy deploy-cocon-site deploy-proxy

# Hetzner server — configure 'hetzner' alias in ~/.ssh/config, or override:
#   make deploy SERVER=root@1.2.3.4
SERVER    ?= hetzner
REMOTE_DIR := /home/app/checkin-automation-agent-first

# Cocon Grenoble static site
#   SITE_BASE      — Astro base path (/cocon now, / for future own domain)
#   STATIC_TARGET  — host dir Caddy serves the cocon site from (mounted :ro)
SITE_BASE     ?= /cocon
STATIC_TARGET := /home/app/unlockers-static/cocon

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

# ---------------------------------------------------------------------------
# Cocon Grenoble static site — build locally, rsync dist/ to the served dir.
#
#   make deploy-cocon-site                  # SITE_BASE=/cocon (default)
#   make deploy-cocon-site SITE_BASE=/      # future own-domain cutover
#
# Astro is built on the BUILD HOST (npm is not assumed on the server); only the
# built dist/ is rsynced. Caddy serves the files live — no proxy restart needed.
# ---------------------------------------------------------------------------

deploy-cocon-site:
	@echo "→ Building site-cocon (SITE_BASE=$(SITE_BASE))"
	cd site-cocon && npm ci
	cd site-cocon && SITE_BASE=$(SITE_BASE) npm run build
	@echo "→ Syncing dist/ to $(SERVER):$(STATIC_TARGET)/"
	rsync -av --delete site-cocon/dist/ $(SERVER):$(STATIC_TARGET)/

# ---------------------------------------------------------------------------
# Caddy reverse proxy — rsync canonical config from deploy/caddy/, then bring
# the stack up and reload (zero-downtime). NEVER `down -v`: caddy_data /
# caddy_config hold the TLS certs.
# ---------------------------------------------------------------------------

deploy-proxy:
	@echo "→ Syncing deploy/caddy/ to $(SERVER):/home/app/caddy-proxy/"
	rsync -av deploy/caddy/ $(SERVER):/home/app/caddy-proxy/
	@echo "→ Bringing up caddy-proxy"
	ssh $(SERVER) 'cd /home/app/caddy-proxy && docker compose up -d'
	@echo "→ Reloading Caddy config"
	ssh $(SERVER) 'docker exec caddy-proxy-caddy-1 caddy reload --config /etc/caddy/Caddyfile'
	@echo "→ Proxy status"
	ssh $(SERVER) 'cd /home/app/caddy-proxy && docker compose ps'
