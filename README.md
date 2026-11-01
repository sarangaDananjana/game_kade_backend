# Meal Drop backend

Shared Django backend for Meal Drop and MealRider. Every kitchen uses the same catalog, checkout, delivery coverage, pricing, and dispatch system.

| App | Models and responsibility |
| --- | --- |
| `users` | `CustomUser`, OTP login, profiles, and app versions |
| `vendors` | `Vendor`, shop ownership, images, coordinates, and pickup availability |
| `products` | Vendor-owned `Product` records and shared `Featured` promotions |
| `orders` | `Order`, `OrderItem`, saved `OrderLocation` addresses, polygon `DeliveryZone` coverage, and `DeliveryDistanceTier` pricing |
| `multivender` | Historical migrations and compatibility imports/routes; no registered models or separate business tables |

Both order API families use `orders.Order`. Product prices come from `products.Product`, and all delivery checks use the existing primary `orders.DeliveryZone` polygon. Orders contain one vendor's items; basket quotes group kitchens independently. Product edits are restricted to the owning vendor or a platform admin. Dispatch and delivery-zone changes require platform admin access.

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

Use Python 3.12 or newer, PostgreSQL with PostGIS, and the dependencies in `requirements.txt`. Linux/Docker also needs GDAL and GEOS; the Dockerfile installs the GIS dependencies.

The existing Docker configuration reads `.env.prod` and starts the application after running migrations and collecting static files:

```sh
docker compose up -d --build
```

Use the existing `DATABASE_URL`, authentication configuration, SMS key, and map key. Route storage reads `MONGO_URI` from the environment. `MONGO_DATABASE` can select a database explicitly; otherwise the database in the existing URI is used, with `MealDrop` as the default for a new connection. Route API families share `MONGO_ROUTE_COLLECTION`, whose default is `multivendor_route_states`.

## Verify

The isolated test settings do not load production environment files or use `DATABASE_URL`.

On Linux, install SpatiaLite and run:

```sh
python manage.py test --settings=food_delivery.test_settings
python manage.py makemigrations --check --dry-run --settings=food_delivery.test_settings
```

On Windows, install `pyogrio` alongside the backend dependencies and set `SPATIALITE_LIBRARY_PATH` to the local `mod_spatialite.dll`. The test settings load its DLL directory and the GDAL/GEOS libraries provided by `pyogrio`.

To test against an isolated PostGIS database, explicitly supply `MEALDROP_TEST_DATABASE_URL`. Django will create a separate test database through that connection. Do not use a production database connection for the test runner.
