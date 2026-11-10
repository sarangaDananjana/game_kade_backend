# Daily ordering hours

In Django admin, open **Orders → Ordering window → Daily ordering hours (Sri Lanka)**.
Set **Opens at** and **Closes at**, then save. Uncheck **Enabled** to temporarily pause new orders.
Changes apply to the next checkout request without restarting the server.

The default remains **12:00 PM to 5:00 PM** daily. Times use **Asia/Colombo**,
regardless of the customer's phone time or the server's configured timezone.
Opening is inclusive and closing is exclusive: an order at exactly 5:00 PM is rejected.
Minutes and seconds are supported. A closing time earlier than opening runs into the
following day; for example, 8:30 PM to 2:15 AM. Equal opening and closing times are invalid.
Existing orders remain accessible and cancellable after closing.

## Deployment

Deploy the updated backend source, run `python manage.py migrate`, and restart the
backend. Migration `orders.0009_ordering_window` creates the settings table and its
single default record. No existing order records are changed.
With the existing Docker deployment, `docker compose --env-file .env.prod up -d --build`
already runs migrations on startup.

The Android source in `C:/Users/Saranga/Documents/MealDrop` now reads the server's
closing time and displays the server's checkout rejection message. Distribute an
updated Android release to replace the old fixed 5 PM labels. The backend enforces
the configured hours for old and new app versions. The browser menu also reads the
schedule from the backend. Displayed hours refresh every minute and when foregrounded;
every checkout checks the latest settings independently.

## API

`GET /api/orders/ordering-window/` is public and read-only, with the compatibility
alias `GET /shop/orders/ordering-window/`. It returns `enabled`, `is_open`,
`opens_at`, `closes_at`, `opens_at_display`, `closes_at_display`, `hours_display`,
`timezone`, `server_time`, and `message`. Responses use `Cache-Control: no-store`.

All customer creation paths (`POST /api/orders/`, `/api/orders/create/`, and
`/shop/orders/create/`) enforce the same schedule. Closed or paused orders return
HTTP 400 before any order or item is written:

```json
{
  "code": "ordering_closed",
  "detail": "Ordering is closed right now. Orders are accepted daily from 12:00 PM to 5:00 PM (Sri Lanka time)."
}
```

Delivery quotes remain available outside ordering hours so customers can inspect prices.
Administrative order entry is unaffected by the customer checkout schedule.

## Verification

```sh
python manage.py test orders --settings=food_delivery.test_settings --noinput
python manage.py makemigrations --check --dry-run --settings=food_delivery.test_settings
```

On Windows, use the local GIS test libraries as described in README.md.
