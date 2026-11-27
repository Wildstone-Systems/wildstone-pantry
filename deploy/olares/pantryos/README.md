# PantryOS on Olares

This local test package uses PantryOS 0.0.5-alpha, pinned to the digest in
its GitHub release manifest. It requires Olares 1.12.6 or newer and installs
one shared PantryOS instance with a private Olares entrance and an
Olares-managed PostgreSQL database.

## Install

1. Sign in to Olares as an administrator. Verify your node can pull the image
   before installing. From the node, an example using its container runtime is:

   ```sh
   sudo crictl pull ghcr.io/wildstone-systems/pantryos@sha256:14d6acd15276fa9ede9e7ae577af05e73ef252a51a91af8886b854bef49131f8
   ```

   If the registry denies the pull, check the GHCR package visibility and
   your node's registry authentication. The packaged chart expects the
   runtime to have access to that image.

2. Generate a Fernet key and store it in a password manager. Use the same
   key for upgrades and restores of this instance.

   ```sh
   python3 -c 'import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'
   ```

3. With Olares CLI signed in, upload the chart, then open PantryOS from
   **Market → Local Sources → Upload** and install it there. Enter the key
   when prompted for `CONFIG_SECRET_KEY`.

   ```sh
   olares-cli market upload ./pantryos-0.1.3.tgz
   ```

4. Open the private PantryOS entrance immediately after install. The
   first-run wizard is available to anyone who can reach that entrance
   before setup is completed. Finish setup before sharing access. Select
   **Accounts** and create the PantryOS administrator.
5. Add a test inventory item and restart the app. Confirm the item survives.
   Then check whether a second Olares user can open the same entrance, sign in
   with a separate PantryOS account, and see the item without admin access.

## Scope

This first chart uses PantryOS accounts. Olares OIDC claim compatibility and
shared entrance behavior still require a live test. The chart provisions a
fresh database and leaves PantryOS's optional application-level scheduled
backups disabled. Verify Olares backup and restore behavior before moving
existing pantry data into this installation.

PantryOS starts locked (`AUTH_ENABLED=true`): every API request needs a
login, a paired device key (`dk_`, which is how a kiosk tablet connects) or an
agent key sent as `X-Agent-Key` (n8n, scripts, AI agents). On first start the
app shows the claim-admin screen. The chart sets `AUTH_PERSONAL_PASSWORD` to
empty so no public default password ever exists.

To rebuild the package from this directory, run `helm package ./pantryos`
from its parent directory.
