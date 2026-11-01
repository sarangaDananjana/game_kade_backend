# Matara delivery area

Customer addresses and delivery orders must lie inside the existing active primary `orders.DeliveryZone` polygon. Its new `district` field is `Matara`, shown in Django admin, the delivery-zone map page, and `/api/orders/delivery-zones/`. Migration `orders.0003_deliveryzone_district` labels existing zones without changing their coordinates, names, active flags, or primary flags.

Coverage uses the actual coordinates before H3 delivery pricing. Points on the polygon boundary qualify. Polygon holes are excluded. An absent, inactive, or invalid primary boundary blocks delivery until it can be checked. Rider subzones do not extend the customer service area. Pickup-only kitchens retain pickup behavior.

Outside locations receive this message:

> We deliver only within the marked area in Matara District. This location is outside it. Choose an entrance inside the marked area to get delivery.

`/shop/locations/` validates both new addresses and updates, including partial edits to an existing address. Successful addresses take their district from the zone. Saved addresses remain in the response and gain `delivery_available` and `delivery_message`; outside addresses are retained so customers can see the warning and replace their entrance.

`POST /shop/orders/delivery-quote/` rejects outside coordinates or saved addresses with HTTP 422 and `code: out_of_delivery_area`. `POST /shop/orders/create/` rejects outside delivery orders before creating any records. The older `/api/orders/locations/` and `/api/orders/` paths enforce the boundary as well.

The Android app shows the message during location checks, while editing an outside address, and on a selected saved address that no longer qualifies. It disables saving and choosing an unavailable address. Its map labels the marked area as the delivery area in Matara District. Missing coverage data cannot enable saving. Backend validation messages are preserved in the save error UI.

## Deployment

Sync the updated backend files from `C:/Users/Saranga/Documents/game_kade_backend`, or extract the supplied `backend-matara-service-area-update.zip` into the backend root. Apply migrations in the deployment environment, then restart the backend:

```sh
python manage.py migrate
```

Keep the existing primary polygon active. No new polygon needs to be drawn. The review APK is `C:/Users/Saranga/Documents/MealDrop/handover/MealDrop-customer-review.apk`.

## Verification

65 backend tests pass with an isolated SpatiaLite database and real GIS libraries, including unchanged polygon migration, inside and outside addresses, partial edits, saved-address flags, quotes, checkout, boundary points, polygon holes, missing coverage, pickup, and the existing pricing and order-code tests. `makemigrations --check --dry-run` detects no pending changes. The Android debug app and instrumentation APK compile; all 23 unit tests and two native UI checks pass. The warning was visually checked on a phone, in dark theme with larger text, and on a compact phone with larger text.

Production deployment and live verification are pending the user's sync. After syncing, run `C:/Users/Saranga/Documents/MealDrop/.design/verify_live_service_area.py` with Python. It checks the Matara metadata, compares the polygon to the captured pre-sync boundary, requests one inside quote and one outside quote, and writes `service-area-live-verification.json`. These quote requests create no orders or addresses.
