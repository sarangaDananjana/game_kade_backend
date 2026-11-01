# Meal Drop multi-vendor upgrade

The shared models adopt the existing multi-vendor data and move its tables into their owning apps. The original single-shop products, orders, and order items are deliberately deleted. No vendor mapping or archive configuration is required.

## Data changes

- Current multi-vendor vendors, products, orders, order items, pricing tiers, IDs, image paths, amounts, timestamps, delivery codes, and assignments are preserved.
- The original `products.Product`, `orders.Order`, and `orders.OrderItem` data is discarded when their obsolete tables are removed. A read-only inspection before this upgrade found 18 old products, 30 orders, and 39 order items.
- Saved addresses from the old `orders.OrderLocation` table are merged into the shared address table. Exact duplicates are reused, and existing multi-vendor address IDs stay valid. The original table contained 19 addresses at inspection time.
- `users.CustomUser`, OTPs, app versions, and the authoritative `orders.DeliveryZone` table remain shared. Existing polygon coordinates, active/primary flags, and district labels are preserved.
- The unused name-only `multivender.DeliveryZone` model and table are removed. They do not supply customer delivery coverage.
- Existing multi-vendor staff/group permissions and valid admin history move to the canonical app labels. Audit entries for discarded single-shop products and orders are removed, and saved-address references are updated.

Deleting the old records is intentional. Reversing the schema migrations recreates empty legacy tables; it does not recover discarded products or orders.

The authorized server cleanup has already deleted those 18 products, 30 orders, and 39 items, plus their obsolete admin history. Retained-data fingerprints matched before and after the transaction: the current 10 multi-vendor products, 28 orders, 28 items, 9 addresses, vendors, users, pricing, polygons, old saved addresses, and shared promotions were unchanged. The new schema migrations still need to be applied when deploying this source.

## Apply the upgrade

Sync the complete backend source, including `core`, `vendors`, the new `orders` services/serializers, and **all** migration files. Keep the historical migrations: Django needs them to upgrade the existing database.

Use the server's existing `.env.prod`. The local environment file now includes the previous route connection as `MONGO_URI`; carry that setting to the server if rider route storage is used. Environment files, uploaded media, and database files are excluded from the review archive.

With the existing Docker setup, build the updated code and inspect the data before restarting the service:

```sh
docker compose build web
docker compose run --rm web python manage.py consolidation_preflight
docker compose run --rm web python scripts/cleanup_obsolete_files.py
docker compose stop web
docker compose run --rm web python manage.py migrate
docker compose up -d web
```

Stop the old web process during the migration because its model classes use the old table names. The service command also runs migrations and collects static files before starting Gunicorn. If deploying outside Docker, run the same cleanup and management commands with the deployment environment loaded, then restart the application.

`cleanup_obsolete_files.py` removes the retired test modules and bytecode from a copied source tree. It does not remove media or database files. With a Git deployment, the tracked deletions are applied normally; the cleanup script also handles source archives copied over an older directory.

The upgrade consists of the following new migrations and their dependencies:

| App | Final migration |
| --- | --- |
| `vendors` | `0002_canonical_table` |
| `products` | `0004_canonical_table` |
| `orders` | `0006_canonical_tables_and_permissions` |
| `multivender` | `0008_retire_unused_zone_model` |

Use `python manage.py migrate` to apply the dependency graph. Do not fake the new migrations.

## Check the deployment and app

Open the Django admin: vendors, products, and orders now appear under their owning apps. The dispatch dashboard is `/dispatch/`, and the two map pages display active vendor locations.

Confirm that `/api/vendors/`, `/api/products/`, `/api/orders/locations/`, and `/api/orders/delivery-zones/` return the current shared records. Both `/shop/orders/` and `/api/orders/` should show the same authenticated customer's orders. Delivery quotes and order creation continue using the configured Matara polygon and parcel pricing.

Deploy the backend before distributing the updated Meal Drop app. The Android source uses `/api/vendors/` and `/api/orders/`; existing clients can continue using `/shop/`. The rider application source was not changed.
