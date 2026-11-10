# Meal Drop backend

Shared Django backend for Meal Drop and Meal Rider. Every kitchen uses the same catalog, checkout, delivery coverage, pricing, and dispatch system.

| App | Models and responsibility |
| --- | --- |
| `users` | `CustomUser`, OTP login, profiles, and app versions |
| `vendors` | `Vendor`, shop ownership, images, coordinates, and pickup availability |
| `products` | Vendor-owned `Product` records and shared `Featured` promotions |
| `orders` | `Order`, `OrderItem`, saved `OrderLocation` addresses, polygon `DeliveryZone` coverage, and `DeliveryDistanceTier` pricing |
| `riders` | Single-vendor `Session` runs, `SessionOrder` attempt history, timed `JobOffer` dispatch, and `RiderInfo` profiles with calculated statistics, scores, cancellation facts, and penalty records |
| `multivender` | Historical migrations and compatibility imports/routes; no registered models or separate business tables |

Both order API families use `orders.Order`. Product prices come from `products.Product`, and all delivery checks use the existing primary `orders.DeliveryZone` polygon. Orders contain one vendor's items; basket quotes group kitchens independently. Product edits are restricted to the owning vendor or a platform admin. Dispatch and delivery-zone changes require platform admin access.

The [rider model guide](riders/README.md) describes session finances and statistics. [MEAL_RIDER_API.md](MEAL_RIDER_API.md) documents timed offers, single-vendor sessions, cancellation/penalty records, scorer-controlled acceptance windows, and the rebuilt Meal Rider Android app.

## API

| Operation | Canonical endpoint |
| --- | --- |
| Login and profiles | `/api/auth/` |
| Kitchens and menus | `/api/vendors/`, `/api/vendors/<id>/` |
| Catalog management | `/api/products/`, `/api/products/<id>/` |
| Saved addresses | `/api/orders/locations/`, `/api/orders/locations/<id>/` |
| Delivery coverage | `/api/orders/delivery-zones/` |
| Basket quote | `POST /api/orders/delivery-quote/` |
| Checkout | `POST /api/orders/create/` |
| Customer history and cancellation | `/api/orders/`, `PATCH /api/orders/<id>/` |
| Meal Rider dashboard and account | `/api/riders/dashboard/`, `/api/riders/profile/` |
| Meal Rider job offers | `/api/riders/jobs/`, `/api/riders/jobs/<offer>/accept/`, `/api/riders/jobs/<offer>/decline/` |
| Meal Rider sessions and handover | `/api/riders/sessions/`, `/api/riders/sessions/<session>/orders/<order>/verify/` |
| Rider assignments | `/api/orders/rider/orders/`, `/api/orders/rider/orders/with-rank/` |
| Rider handover | `PATCH /api/orders/rider/orders/<id>/` |
| Rider routes | `/api/orders/rider/routes/optimize/`, `/api/orders/rider/routes/current/` |
| Browser dispatch dashboard | `/dispatch/` |
| Browser customer menu | `/menu/` |

The existing `/shop/` customer, rider, and dispatch paths remain aliases for the shared handlers. Meal Drop's Android `ApiService.kt` uses the canonical paths. Authentication, order request/response fields, current multi-vendor IDs, image paths, delivery quotes, and numeric QR/PIN behavior are retained.
Bookmarks for the former `/admin/multivender/` model pages redirect to their owning apps.

## Upgrade from the old shop backend

Follow [MULTIVENDOR_MIGRATION.md](MULTIVENDOR_MIGRATION.md). The upgrade deliberately deletes the old single-shop products, orders, and order items, as requested for this development system. It preserves current multi-vendor records, shared users, delivery polygons, pricing settings, and saved addresses.

## Run

Daily customer ordering hours are configurable in Django admin under **Orders → Ordering window**.
See [ORDERING_WINDOW.md](ORDERING_WINDOW.md) for configuration, API behavior, and deployment.

Use Python 3.12 or newer, PostgreSQL with PostGIS, and the dependencies in `requirements.txt`. Linux/Docker also needs GDAL and GEOS; the Dockerfile installs the GIS dependencies.

The Docker configuration reads `.env.prod`, runs migrations and collects static files, then starts the web application and the rider offer dispatcher:

```sh
docker compose --env-file .env.prod up -d --build
```

Keep the existing `DATABASE_URL`, SMS key, and map key, and supply a new private `SECRET_KEY` for this security upgrade. Route storage reads `MONGO_URI` from the environment. `MONGO_DATABASE` can select a database explicitly; otherwise the database in the existing URI is used, with `MealDrop` as the default for a new connection. Route API families share `MONGO_ROUTE_COLLECTION`, whose default is `multivendor_route_states`.

Request limits use the private Redis service included in Docker Compose. Production now requires a private `SECRET_KEY` and Redis, trusts only configured proxy addresses, and enforces HTTPS and request/upload limits. Follow [SECURITY.md](SECURITY.md) when deploying this security upgrade, including the Nginx changes and signing-key rotation. Rate budgets can be tuned through `.env.prod`; exceeded budgets return HTTP `429` with `Retry-After`.

## Verify

The isolated test settings do not load production environment files or use `DATABASE_URL`.

On Linux, install SpatiaLite and run:

```sh
python manage.py test --settings=food_delivery.test_settings
python manage.py makemigrations --check --dry-run --settings=food_delivery.test_settings
```

On Windows, install `pyogrio` alongside the backend dependencies and set `SPATIALITE_LIBRARY_PATH` to the local `mod_spatialite.dll`. The test settings load its DLL directory and the GDAL/GEOS libraries provided by `pyogrio`.

To test against an isolated PostGIS database, explicitly supply `MEALDROP_TEST_DATABASE_URL`. Django will create a separate test database through that connection. Do not use a production database connection for the test runner.
