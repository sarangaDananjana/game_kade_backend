# Django admin save errors

The Riders admin now displays save-time validation and conflicting-record errors
on the submitted form. Parent, delivery-attempt, wallet and audit-log database
changes roll back together when a save fails. Session-list updates also roll
back if their audit-log write fails. Bulk finance confirmation skips invalid
sessions and reports their errors while confirming valid sessions.

Confirmed session facts are read-only. Previously an unchanged form could lose
timestamp microseconds and fail the model's exact comparison of confirmed facts.
Staff can still save notes on confirmed sessions.

The backend defaults `DATABASE_DISABLE_SERVER_SIDE_CURSORS=True`. This supports
the Supabase transaction pooler on port 6543, where a named cursor may be fetched
on a different PostgreSQL session. Django admin foreign-key choices use these
cursors by default. Keep this setting enabled for transaction pooling. A direct
or session-pooled connection can optionally set it to `False` when streaming
large query results matters.

## Deploy

Upload/sync the updated backend files to the server, keeping its `.env.prod`,
then rebuild the two backend services from `~/game_kade_backend`:

```sh
docker compose --env-file .env.prod up -d --build web rider_dispatcher
```

These fixes introduce no database migration. Existing unrelated pending
migrations still run as part of the configured web startup command. No database
URL change is required.

## Capture an error

Run this as one line, then reproduce the save in admin:

```sh
docker compose --env-file .env.prod logs --follow --tail 100 web rider_dispatcher
```

Or retrieve recent logs:

```sh
docker compose --env-file .env.prod logs --since 30m --tail 300 web rider_dispatcher
```

Compose accepts service names (`web`, `rider_dispatcher`), while `docker logs`
accepts container names or IDs:

```sh
docker logs --since 30m --tail 300 game_kade_backend-web-1
```

Unexpected Django request tracebacks now go to stderr with `DEBUG=False`.
Dispatcher database errors also include a traceback. Keep debug disabled in
production. Redact credentials and personal data before sharing logs.

`Autorestarting worker after current request` is normal Gunicorn rotation from
the configured `--max-requests` limit. The current container health check tests
the listening port; it does not verify database connectivity or admin saves.

## Choosing a database host

Moving PostgreSQL does not fix validation errors or an incompatible cursor
configuration. Inspect the traceback first. `InvalidCursorName` or `cursor ...
does not exist` suggests transaction-pooler cursor handling. Connection errors,
connection exhaustion and `WORKER TIMEOUT` require checking database availability,
connection capacity, locks and request duration from the application server.

If logs establish a persistent problem with the external database, a separate
PostgreSQL/PostGIS service on the same server is an option. This application
requires PostGIS; the plain PostgreSQL containers used by other applications
are not a ready replacement. A move needs an existing-data backup and restore,
PostGIS verification, private networking, ongoing backups, and a coordinated
cutover for both web and rider dispatcher. The server already runs several
applications, so check spare memory and disk before adding a database.
