# Installing PantryOS

PantryOS is a household operations app — pantry inventory, recipes, meal
planning, shopping. It runs entirely on your own machine. Nothing you enter
leaves it.

This guide is everything you need. You do not need the source code.

---

## What you need

- A machine that stays on: a laptop, a mini PC, a NAS, or a Raspberry Pi.
  Linux, macOS and Windows all work.
- **Docker Desktop** (macOS/Windows) or **Docker Engine + Compose v2** (Linux).
  Get it at <https://docs.docker.com/get-started/get-docker/>.
- The files you were sent: `docker-compose.yml`, `.env.example`, and this
  guide. They hold no secrets — that is exactly what makes them safe to hand
  over, and why you get them directly rather than from a download page.
- A one-time sign-in to GitHub's container registry (below) so Docker can
  download the PantryOS image.

Everything runs on your local network. No account, no cloud service, no
subscription is involved.

---

## 1. Sign in to the image registry

If the PantryOS image package is public, skip this step. If it is still
private, Docker needs to know who you are: you will have been sent a personal
access token (a long string) that can only read packages. Run this once,
replacing `YOUR_GITHUB_USERNAME` with your own:

```bash
docker login ghcr.io -u YOUR_GITHUB_USERNAME
```

Paste the token when it asks for a password. It is not echoed to the screen.
This is stored by Docker and you will not have to do it again.

> The token only permits downloading PantryOS images, and it can be revoked at
> any time without affecting your installation.

## 2. Set up a folder

```bash
mkdir pantryos
cd pantryos
```

Put the `docker-compose.yml` and `.env.example` you were sent in it.

## 3. Create your configuration

```bash
cp .env.example .env
```

Then generate your two secrets. This one command prints both as finished
lines, ready to paste into `.env` over the two empty ones:

```bash
docker run --rm python:3.11-slim python -c "import base64,os,secrets;print('POSTGRES_PASSWORD='+secrets.token_hex(24));print('CONFIG_SECRET_KEY='+base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

It runs the same on Windows, macOS and Linux, and needs nothing installed
beyond the Docker you already have.

Open `.env` in a text editor and paste them in. The rest of the file is
optional — a different port, or requiring a login — and every line is
commented.

**Keep this file.** It holds the password to your own database. If you lose it,
you lose access to your data.

## 4. Start it

```bash
docker compose up -d
```

The first run downloads a few hundred megabytes and sets up the database, so
give it a couple of minutes. After that, starts are quick.

## 5. Open it

Go to <http://localhost:8420>.

A first-run wizard walks you through naming your household, choosing whether
you want a login, and adding some starter data. Every step is skippable.

To use PantryOS from your phone or a tablet, find this machine's local network
address and open `http://THAT_ADDRESS:8420` on the other device. On Linux:

```bash
hostname -I | awk '{print $1}'
```

On macOS, it is in System Settings → Network. On Windows, run `ipconfig`.

---

## Everyday commands

Run these from inside your `pantryos` folder.

| What you want | Command |
| --- | --- |
| Check it is running | `docker compose ps` |
| See what went wrong | `docker compose logs -f pantryos` |
| Stop it (data is kept) | `docker compose stop` |
| Start it again | `docker compose start` |
| Shut it down (data is kept) | `docker compose down` |

---

## Updating

Updates are explicit — nothing changes underneath you.

1. Back up first (see below). Always.
2. Edit `.env` and change `PANTRYOS_VERSION` to the new version.
3. Run:

```bash
docker compose pull
docker compose up -d
```

The new version applies any database changes it needs automatically on
startup, and keeps all of your data.

### Rolling back

If a new version misbehaves, set `PANTRYOS_VERSION` back to the previous
value and run the same two commands.

One caveat worth knowing: rolling back the app does **not** roll back the
database. If the newer version changed the database structure, the older
version may not start against it. That is what the pre-update backup is for —
restore it, then start the older version. If a rollback does not come straight
back up, say so rather than trying to repair it; that is exactly the kind of
report this beta is looking for.

---

## Your data, and how to keep it

Everything you enter — inventory, recipes, recipe photos, meal history —
lives in a Docker volume called `pantryos_data`. Recipe photos are stored in
the database too, so there is no second place to remember.

**This matters:**

- `docker compose down` stops PantryOS and **keeps** your data.
- `docker compose down -v` stops PantryOS and **permanently deletes** it.
  There is no undo. Do not use `-v` unless you intend to start over.

### Backups

PantryOS takes a nightly snapshot on its own and keeps the last 7 days plus 4
weekly ones. Find them under the admin screens in the app, where you can also
take one on demand and download it.

Before you update, and periodically during the beta, take a copy off the
machine:

```bash
# 1. Take a backup now, then download it from the app's admin screen, or:
docker compose exec -T postgres pg_dump -Fc -U pantryos_app pantryos > pantryos-backup.dump
```

Keep that file, plus your `.env`, somewhere else. Together they are everything
needed to rebuild your install.

### Restoring

PantryOS builds its tables the first time it starts, so by the time you want to
restore, the database is not empty — and restoring on top of existing tables
leaves a half-merged mess rather than your backup. Empty it first:

```bash
# 1. Stop the app so nothing writes while you work. Leave the database running.
docker compose stop pantryos

# 2. Empty the database.
docker compose exec -T postgres psql -U pantryos_app -d pantryos \
  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

# 3. Restore your backup into it.
docker compose exec -T postgres pg_restore --no-owner -U pantryos_app -d pantryos < pantryos-backup.dump

# 4. Start the app again.
docker compose start pantryos
```

Step 2 deletes everything currently in the app. That is the point — you are
replacing it with the backup — but there is no undo, so be sure you are
restoring the file you meant to.

Check that PantryOS comes back up (`docker compose ps`, then open it in a
browser) before assuming the restore worked.

---

## Who can reach PantryOS

By default, anyone on your local network can. That is the intended setup — it
is how you use PantryOS from the kitchen tablet — but it is worth being
deliberate about:

- **Kiosk mode is the default.** There is no login screen, and anyone who can
  open the address has full access, including the admin screens. On a home
  network you control, that is usually what you want.
- **To require a login**, use the first-run wizard's "household accounts"
  option, or set `AUTH_ENABLED=true` in `.env` before the first start.
- **To confine it to this one machine**, set `PANTRYOS_BIND=127.0.0.1` in
  `.env` and restart.
- **PantryOS is not exposed to the internet** by any of this. Making it
  reachable from outside your home would require deliberately configuring your
  router or a tunnel, and it is not recommended during beta.

Traffic on your network is plain HTTP, not HTTPS. Treat it the way you would
treat a printer or a smart speaker: fine on your own network, not something to
put on the public internet.

---

## When something breaks

That is the point of a beta — please report it. Useful things to include:

```bash
docker compose ps
docker compose logs --tail=100 pantryos
```

Plus the version from `.env` and what you were doing at the time. There is
also a feedback form inside the app.

## Uninstalling

```bash
docker compose down          # keeps your data
docker compose down -v       # deletes your data too
```

Then delete the `pantryos` folder.
