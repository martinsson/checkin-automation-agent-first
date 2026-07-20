# Manual server steps (pre-Ansible runbook)

Things currently done **by hand** on the Hetzner box. This is a holding list so we
don't forget them — each item should eventually become an Ansible task.

## Payment reconciliation cron (`scripts/payment_reconcile.py`)

1. **Deploy the latest code** — from a workstation, at the repo root:
   ```bash
   make deploy
   ```
   Rsyncs the source, rebuilds the image and `docker compose up -d`, so the new
   `scripts/payment_reconcile.py` lands inside `checkin-automation-agent-first-web-1`.

2. **Check `.env` on the server** (`/home/app/checkin-automation-agent-first/.env`)
   has the keys the script needs:
   - `BEDS24_READ_ALL_TOKEN` (reads) and `BEDS24_REFRESH_TOKEN` (mints the write
     token for `--mark-paid`).
   - `EMAIL_USER` / `EMAIL_PASSWORD` / `EMAIL_IMAP_HOST=imap.gmail.com` /
     `EMAIL_IMAP_PORT=993`. **`EMAIL_USER` must be the mailbox that receives the
     BP "virement reçu" alerts** (including the ones forwarded from the 2nd
     mailbox), or the script finds no credits.

3. **Create the log directory:**
   ```bash
   ssh hetzner 'mkdir -p /home/app/logs'
   ```

4. **Smoke-test once (report only, no writes):**
   ```bash
   ssh hetzner 'docker exec checkin-automation-agent-first-web-1 \
     python3 scripts/payment_reconcile.py --days 7'
   ```

5. **Install the daily cron** (08:00 Europe/Paris, 7-day rule, auto-mark-paid,
   e-mail recap):
   ```bash
   ssh hetzner "( crontab -l 2>/dev/null | grep -v payment_reconcile; \
     echo 'CRON_TZ=Europe/Paris'; \
     echo '0 8 * * * docker exec checkin-automation-agent-first-web-1 python3 scripts/payment_reconcile.py --days 7 --mark-paid --email-to martinsson.johan@changit.fr >> /home/app/logs/payment_reconcile.log 2>&1' \
   ) | crontab -"
   ```

## Prerequisites configured outside the server (manual, one-time)

- **BP e-banking:** e-mail alert on **received** transfers enabled, detailed
  format, for the account(s) that receive guest transfers.
- **2nd mailbox:** auto-forward the BP alert e-mails to the connected inbox
  (`martinsson.johan@changit.fr`).

## To turn into Ansible later

- Template `.env` from a vault (secrets), never committed.
- Ensure `/home/app/logs` exists.
- Render + install the crontab entry (idempotent).
- Log rotation for `/home/app/logs/payment_reconcile.log`.
