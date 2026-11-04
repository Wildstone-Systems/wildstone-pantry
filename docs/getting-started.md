# Getting started

Pick the option that fits your setup. All of them run PantryOS entirely on
your own machine.

| Option | Best for |
|---|---|
| [One-line install](#one-line-install-linux-nas-raspberry-pi) | Linux, NAS, Raspberry Pi |
| [Docker Compose](#docker-compose) | Anyone comfortable with Docker |
| [Release bundle](#release-bundle-no-source-code) | Testers who don't want the source |
| [Desktop launcher](#desktop-launcher-windows-macos) | Windows / macOS |

**Requirement for all options:** Docker Engine + Compose v2 (Linux) or Docker
Desktop (Windows/macOS) — <https://docs.docker.com/get-started/get-docker/>.

## One-line install (Linux, NAS, Raspberry Pi)

Review the script first, then run:

```bash
curl -fsSL https://raw.githubusercontent.com/Wildstone-Systems/wildstone-pantry/main/scripts/install-product.sh | bash
```

- Generates secrets into `~/.pantryos`
- Starts the product stack
- Prints the address to open (default port **8420**; override with `PANTRYOS_PORT`)

## Docker Compose

From a checkout of this repo:

```bash
POSTGRES_PASSWORD=$(openssl rand -hex 24) \
CONFIG_SECRET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") \
docker compose -f docker-compose.product.yml up --build -d
```

Open <http://localhost:8420>.

- One container serves both the UI and the API on a single port.
- Postgres is not exposed outside the stack.
- The published image is `ghcr.io/wildstone-systems/pantryos` (built on `v*` tags).

## Release bundle (no source code)

Every GitHub Release includes the [`release/`](../release/) bundle: an
image-only `docker-compose.yml`, `.env.example` and a step-by-step guide.
Follow [`release/README.md`](../release/README.md).

## Desktop launcher (Windows, macOS)

A small app that checks for Docker, generates config, starts the stack and
opens your browser. See [`launcher/README.md`](../launcher/README.md).

## First run

1. Open PantryOS in your browser.
2. The setup wizard asks for a household name, an admin account and whether to
   seed default locations (Fridge, Freezer, Pantry…).
3. Start adding items — scan a barcode, import a receipt, or add by hand.

## Next steps

- [Single sign-on (OIDC)](sso-oidc-setup.md)
- [Connect an AI assistant (MCP)](mcp-and-agents.md)
