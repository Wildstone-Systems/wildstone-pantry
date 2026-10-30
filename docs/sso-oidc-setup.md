# Setting up single sign-on (OIDC)

PantryOS supports native OIDC login (ADR 0020) so a homelab that already runs
an identity provider doesn't need a second set of passwords. This is a short
walkthrough for the common combo — **LLDAP** as the directory, fronted by
**Authelia**, **authentik**, **Keycloak**, or **Pocket ID** as the OIDC
provider — but any standards-compliant OIDC provider works.

SSO is entirely free and self-hosted: it touches no PantryOS Connect service
and needs no environment variables. Everything is configured from the admin
UI.

## 1. Create two groups in LLDAP (or your directory)

- `pantryos_users` — anyone allowed to log in to PantryOS.
- `pantryos_admin` — anyone who should land as a PantryOS admin.

Add household members to `pantryos_users`; add yourself (or whoever manages
the household) to `pantryos_admin` too. Membership is re-checked on every
login, so removing someone from `pantryos_users` later locks them out, and
removing them from `pantryos_admin` demotes them to a regular member on their
next sign-in.

Group names, and the claim they're read from, are configurable in step 3 if
your directory uses different names.

## 2. Register PantryOS as an OIDC client

In your IdP (Authelia, authentik, Keycloak, Pocket ID, ...), create a new
OIDC/OAuth2 client:

- **Grant type**: Authorization Code with PKCE.
- **Redirect URI**: shown read-only on the PantryOS admin Integrations page,
  under **Single sign-on** — copy it with the button there. It looks like
  `http://<your-pantryos-host>/api/auth/oidc/callback`.
- **Scopes**: `openid profile email groups` (PantryOS needs `email` to
  identify the user and a groups claim to gate access).

Save the client and note the **issuer URL**, **client ID**, and **client
secret** the IdP gives you.

## 3. Configure PantryOS

In the PantryOS admin UI, go to **Integrations → Single sign-on** and fill in:

- **Issuer** — the IdP's issuer URL (PantryOS fetches
  `<issuer>/.well-known/openid-configuration` from this).
- **Client ID** / **Client secret** — from step 2.
- **Scopes** — defaults to `openid profile email groups`; change only if your
  IdP needs different scopes.
- **Groups claim** — the claim name carrying group membership (default
  `groups`).
- **Users group** / **Admin group** — the group names from step 1 (defaults
  `pantryos_users` / `pantryos_admin`).
- **Button label** — what the SSO button says on the login page (default
  "Sign in with SSO").

Save. The login page immediately starts showing a "Sign in with SSO" button
above the password form.

## 4. Optional: hide the local password form

Once OIDC is configured, a **"Hide local password login"** switch appears on
the same card. Turning it on collapses the email/password form behind a
"Use local admin login" link on the login page — cosmetic only. The
`/api/auth/login` endpoint is never disabled, on purpose: if your IdP
container is down, click the reveal link and log in with the bootstrap admin
password. This break-glass path always works, even with the toggle on.

## Notes

- Local password login always stays available server-side, regardless of
  the toggle — that's the deliberate break-glass path described above.
- Role is re-evaluated on every SSO login. An IdP-side group change (adding
  or removing someone from `pantryos_admin` or `pantryos_users`) takes effect
  the next time that person logs in.
- Only email-based account matching is supported — a user's PantryOS account
  is matched by the `email` claim from the IdP, not an internal `sub`.
