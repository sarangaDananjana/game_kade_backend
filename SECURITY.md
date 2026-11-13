# Backend request protection

The application now rejects excessive requests before authentication or database work. Redis counters are shared by all Gunicorn workers and checked/incremented atomically. Nginx adds connection, buffering, and request-rate limits before requests reach Python.

## Default budgets

Every budget listed for an operation applies. An hour budget remains in effect when a minute budget resets.

| Operation | Identity | Default budgets |
| --- | --- | --- |
| All Django requests, including errors and media | Client IP | 240/minute; 10,000/hour |
| Authenticated API requests | Verified user ID | 120/minute; 5,000/hour |
| Authenticated API writes | Verified user ID | 30/minute |
| OTP requests | Client IP | 5/minute; 30/hour |
| OTP issuance/resend | Normalized phone | 1/minute; 5/hour |
| OTP requests across the whole service | Shared | 30/minute; 300/hour |
| OTP verification | Client IP | 15/minute; 100/hour |
| OTP verification | Normalized phone | 10/minute; 30/hour |
| Token refresh | Client IP | 20/minute |
| Admin and dispatch login | Client IP | 5/minute; 30/hour |
| Admin and dispatch login | Normalized account | 10/hour |
| Checkout | Verified user ID | 5/minute; 50/hour |
| Basket quotes | User ID, or IP for anonymous browsing | 20/minute |
| Paid route optimization | Verified user ID | 3/minute; 30/hour |

Canonical and `/shop/` aliases share budgets. OTP phone budgets normalize local, `+94`, and `0094` numbers. The initial name-entry step does not consume the phone resend cooldown; it still consumes the IP and global OTP-request budgets. The global OTP budget also bounds account creation and SMS spending during a distributed attack.

These are fixed windows aligned to Unix time; a short burst can cross a window boundary. The Nginx rate limit provides an additional continuous limit. Tune the budgets for actual rider polling, customer traffic, and shared networks, rather than increasing them to hide an attack.

Set overrides such as `RATE_LIMIT_IP=240/minute,10000/hour` or `RATE_LIMIT_ROUTE_OPTIMIZE=3/minute,30/hour` in `.env.prod`. `.env.example` lists every supported override. Counts must be positive and units must be `second`, `minute`, `hour`, or `day`. Empty or malformed limits prevent startup.

Blocked application requests return `429`, a JSON `code` of `rate_limited`, `Cache-Control: no-store`, and a `Retry-After` header in seconds. Clients should wait at least that long before retrying; repeated immediate retries will remain blocked. A Redis outage or full Redis memory returns `503` with `Retry-After: 30`, because allowing unmetered requests would remove the guard.

## Authentication and input safeguards

- Production reads `SECRET_KEY` from the environment and rejects missing, short, obviously weak, and `django-insecure-` keys. API endpoints require authentication by default; explicitly public menus, catalog reads, OTPs, and quotes remain public.
- OTPs use a cryptographic random generator. The fixed `0700000000` / `00000` login bypass is removed. The owner accounts `0774610536` and `0721992125` always skip SMS and still require an unpredictable six-digit OTP visible in the protected admin. `OTP_SMS_BYPASS_NUMBERS` adds and normalizes other exclusions; an empty setting does not remove the owner exclusions.
- Five failed guesses invalidate an OTP in the database, even when an attacker changes IPs. Issuance and verification lock the user row, preventing simultaneous verification from issuing multiple logins. A resend invalidates every previous code, and an explicitly rejected SMS leaves no usable code. A lost provider response returns `202` with unconfirmed delivery; the code remains verifiable for its normal five-minute lifetime, with no automatic SMS retry. Disabled accounts stay disabled.
- JWT refresh rotation now has the blacklist app installed. A previously rotated refresh token is rejected. The access-token lifetime remains one hour, and refresh lifetime remains seven days.
- JSON and other non-file request bodies are capped at 1 MiB. Multipart requests are capped at 10 MiB, each file at 5 MiB, and forms at 1,000 fields and five files. Oversized bodies/files return `413`; excessive form fields/files return `400`. Rejected active temporary uploads are closed.
- Login/profile fields and map coordinates are validated. External SMS/map requests have timeouts and do not follow redirects. Map/storage failures return generic errors instead of exposing connection strings or map keys.
- HTTPS redirects, secure session/CSRF cookies, HttpOnly sessions, `SameSite=Lax`, MIME-sniffing protection, frame protection, and a strict referrer policy are enabled. Initial HSTS duration is one day; subdomain inclusion and preload are disabled.
- Known vulnerable Django, DRF, Pillow, JWT, SQL parser, certificate, and HTTP dependencies were upgraded to fixed versions. Rebuild the container to install them; restarting an existing image keeps its old packages.

## Deploy on the existing Nginx host

1. Preserve the current database, media, and provider configuration. Add the security settings from `.env.example` to the server's `.env.prod`. Generate a **new** private `SECRET_KEY` of at least 50 characters, for example with `python -c "import secrets; print(secrets.token_urlsafe(64))"`, and store it only in `.env.prod` or your secret manager. Retiring the previously hard-coded signing key invalidates existing tokens and browser sessions; users must log in again. Do not reuse that key as an environment value.
2. Set `RATE_LIMIT_REDIS_URL=redis://rate_limit_cache:6379/0`. Docker creates a persistent, private Redis instance with a 64 MiB counter limit and `noeviction`; it publishes no Redis port. Local counters are available only with `DEBUG=True` and an empty Redis URL. Production refuses to start without Redis configuration. Do not flush rate counters during an attack.
3. The Docker backend network defaults to `172.30.0.0/24` with host gateway `172.30.0.1`. Set `TRUSTED_PROXY_CIDRS=127.0.0.1/32,::1/128,172.30.0.1/32` for Nginx on this host. If that subnet conflicts with another network, change `MEALDROP_NETWORK_SUBNET`, `MEALDROP_NETWORK_GATEWAY`, and the trusted gateway address together. A custom external Redis service needs the same atomic scripting capability and should use `rediss://` and authentication outside the private Docker network.
4. Adapt [deploy/nginx.conf.example](deploy/nginx.conf.example) to the existing site and certificate paths. Keep existing certificate-renewal routes. Preserve `proxy_pass http://127.0.0.1:8002`, overwrite `X-Forwarded-For` with `$remote_addr`, and set `X-Forwarded-Proto` to `$scheme`. Do not trust arbitrary client-supplied proxy headers. Apply the request/connection limits and timeouts, then validate with `sudo nginx -t` before `sudo systemctl reload nginx`.
5. Build and start with `docker compose --env-file .env.prod up -d --build`. The web service runs the OTP-attempt and JWT-blacklist migrations before Gunicorn starts. Port 8002 remains bound to loopback. Worker timeouts, header limits, request recycling, and container CPU/memory caps are included; adjust the caps for the host's available capacity.
6. Run `docker compose --env-file .env.prod exec web python manage.py check --deploy`. Check normal customer login, admin login, rider polling, checkout, and an image upload. Confirm Nginx reports distinct client IPs and that a trusted HTTPS request reaches Django as secure; incorrect proxy settings can cause redirects or combine everyone into one rate bucket.
7. Schedule `docker compose --env-file .env.prod exec -T web python manage.py flushexpiredtokens` daily to remove expired blacklist/outstanding-token records. Watch `429`/`503` counts, Redis memory, and provider spending; an excessive global SMS budget defeats its cost protection.

Code changes and request limits do not remove malware or restore stolen credentials. Following the reported attack, rotate any exposed SMS/map/database, SSH, and administrator credentials, review unexpected processes and outbound traffic, and rebuild the host if it was compromised. Large attacks that saturate the network link also require filtering at the hosting provider or a CDN/WAF.

## Verification

Tests use `food_delivery.test_settings`, ignore production `.env` files and `DATABASE_URL`, and explicitly enable isolated rate counters for security cases. Run:

```sh
python manage.py test --settings=food_delivery.test_settings
python manage.py makemigrations --check --dry-run --settings=food_delivery.test_settings
```

On Windows, use the GIS setup documented in the README. To exercise the production Lua script against a **dedicated test Redis instance**, additionally set `MEALDROP_RATE_LIMIT_TEST_REDIS_URL=redis://127.0.0.1:16379/0` and run `python manage.py test core --settings=food_delivery.test_settings`. These cases use unique counter identities and never flush the database. They test independent concurrent Redis clients and sustained budgets across window resets.
