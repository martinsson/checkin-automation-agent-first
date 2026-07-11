# Deploy

Infrastructure config that lets the Caddy reverse proxy **and** the Cocon
Grenoble static site deploy from this repo, with no manual file editing on the
server.

## What's here

- `caddy/Caddyfile` — canonical Caddy config, single source of truth. Mirrors
  the server's `/home/app/caddy-proxy/Caddyfile` byte-for-byte. Serves three
  site blocks: `unlockers.ai` (+www) static, `rental.changit.fr`
  (`/cocon/*` static handle + redirect, fallback `reverse_proxy :8000`), and
  `rental2.changit.fr` → `:8001`.
- `caddy/docker-compose.yml` — the caddy-proxy compose, mirrored from the
  server. `caddy:alpine`, ports 80/443 (+udp), mounts `./Caddyfile` and the
  static root `/home/app/unlockers-static` read-only, named volumes
  `caddy_data` / `caddy_config`.

## Deploy targets

Run from the repo root:

- `make deploy-cocon-site` — build the Astro site (`site-cocon/`) locally with
  `SITE_BASE` (default `/cocon`) and rsync `dist/` to
  `hetzner:/home/app/unlockers-static/cocon/`. No Caddy restart needed; Caddy
  serves static files live.
- `make deploy-proxy` — rsync `deploy/caddy/` to
  `hetzner:/home/app/caddy-proxy/`, then `docker compose up -d`, reload Caddy
  (zero-downtime), and print `ps`.

Override the server with `make deploy-cocon-site SERVER=root@1.2.3.4`, or the base
path with `make deploy-cocon-site SITE_BASE=/`.

## One-time bootstrap (manual)

These are NOT automated — do them once before the first deploy:

1. Configure the SSH alias `hetzner` in `~/.ssh/config` (host, user, key).
2. Create the server directories:
   ```
   ssh hetzner 'mkdir -p /home/app/caddy-proxy /home/app/unlockers-static/cocon'
   ```
3. Run `make deploy-proxy` once, then `make deploy-cocon-site`.

## Reconcile caveats (read before first `deploy-proxy`)

- **`unlockers.ai` content is externally managed.** The Caddyfile here
  references `unlockers.ai` (and the static mount also holds
  `business-people-coding`, `en`, etc.), but only the `cocon/` subdir belongs to
  this repo. Caddy just serves whatever is mounted under
  `/home/app/unlockers-static`; this repo does not deploy that content.
- **The `:8000` backend belongs to a different project.**
  `rental.changit.fr` → `:8000` is `checkin-automation-web-1` from
  `/home/app/checkin-automation/` (a separate project dir). The Caddyfile
  references it but this repo does not manage or deploy that container.
- **Never `docker compose down -v`** for the caddy-proxy stack. The named
  volumes `caddy_data` / `caddy_config` hold the Let's Encrypt TLS certs;
  `-v` would destroy them. `deploy-proxy` only ever does `up -d` + reload.
- The first `deploy-proxy` overwrites the live server `Caddyfile`. The repo copy
  is byte-equivalent to the current server file, so nothing on
  `unlockers.ai` / `:8000` should change.
