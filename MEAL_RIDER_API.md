# Meal Rider backend and application

## Rider top-up balance and road geometry (2.3.0)

Run rider migration `0006` before deploying this app. Existing balances start at zero. Staff collect money, then use admin → Riders → Rider top ups → Add to credit the rider. Enter the amount, select the rider, and optionally add a collection reference/note. Saving creates the credit and ledger entry transactionally, with the staff identity and timestamp. Credits and wallet ledger entries are immutable in admin; the profile's `topup_balance` is read-only. No rider endpoint accepts top-ups. Card top-ups in the Android app are disabled with Coming soon.

`GET /api/riders/wallet/` returns `balance`, `acceptance_percent` (20), and the latest 100 `transactions` with signed `amount`, `kind` (`topup` or `job_fee`), `balance_after`, `session_reference`, and `created_at`. Profile reads include `topup_balance`. Offers include `acceptance_fee` and `acceptance_percent`. The fee is 20% of rider cut plus fuel, rounded half up to cents. A Rs. 240 offer costs Rs. 48 to accept. `POST /api/riders/jobs/{id}/accept/` optionally accepts `expected_acceptance_fee`; a changed quote returns `409 offer_changed`. Insufficient funds return `409 insufficient_topup_balance` without altering the offer/orders. The fee, offer acceptance and order assignment commit together. Repeating a successful acceptance returns the same session without a new debit. Success includes `topup_balance_after`. Existing active sessions are not charged retrospectively; cancellation does not automatically refund the fee. Gross earnings and penalties remain separate.

`GET /api/riders/sessions/{id}/route/` is authorized only for the assigned rider or a current addressed offer. It returns `status`, encoded Google `polylines`, `distance_meters`, `duration_seconds`, `copyrights`, and `expires_at`; pending responses include `retry_after_seconds`. The backend requests Google Directions driving routes in the existing kitchen/stop order. Identical ordered coordinates share a 24-hour database cache across riders and workers. A 30-second lease suppresses concurrent paid calls. Failed requests are cached for five minutes. Larger routes are split at Google's 25-waypoint limit, with at most one new paid segment per endpoint request. Polling jobs/dashboard never calls Google; geometry is requested only when viewing maps. The app reuses geometry in memory and shows markers plus a message if routing is unavailable.

The existing `GOOGLE_MAPS_API_KEY` is used by default and was verified once against Directions. Optionally configure `GOOGLE_DIRECTIONS_API_KEY` for a separately restricted server key. Preserve the Android Maps key and application ID. Bank/card payment processing is not introduced in this update.

Meal Rider uses the shared Meal Drop users, vendor catalog, and orders. A delivery session is bound to **one vendor**. Session offers, accepted runs, location clusters, verification, cancellation history, scores, and penalties now have rider APIs.

## Rider profile

`RiderInfo` retains its existing availability and statistics helpers and adds:

- `job_acceptance_window`: seconds granted to this rider for each new offer, constrained to 5–600 seconds; the new-profile fallback is 60.
- `score`, `score_updated_at`, and `score_version`: latest facts provided by the dedicated scoring backend.
- `cancelled_sessions_count` and `last_cancelled_at`, backed by timestamped `RiderCancellation` records with reason, stage, released orders, and replacement session.
- `total_penalties_count`, `last_penalty_at`, `penalty_balance`, and `penalty_points`, backed by `RiderPenalty` records with idempotency references, reason, amount, points, and resolution status.
- Vehicle type/registration, `vehicle_name` (make/model), `vehicle_colour`, emergency contact, and optional profile photo.

The scorer updates `job_acceptance_window` together with the rider's score. A new `JobOffer` snapshots that window into `acceptance_window` and `expires_at`; changing a profile does not change the deadline on a pending offer. Riders cannot edit scores, penalties, or acceptance windows through their account API. No automatic scoring or monetary cancellation penalty has been invented.

`profile_completion` is derived from the shared user's name, vehicle details, and emergency contact. Its three tasks include titles, descriptions and `is_complete`, with completed/remaining totals. Existing details count immediately; bicycles do not require a registration number. Profile PATCH responses return updated counts. The counts are computed; rider migration `0005` adds the vehicle name/colour fields. The app shows setup once and keeps unfinished-task reminders in Dashboard and Account; all tasks must be complete before automatic/directed offers, acceptance, or legacy order assignment. Existing active runs can still be resolved.

## Job lifecycle

Managers create a planned session from active delivery orders belonging to one vendor. Its quoted rider cut, platform service charge, fuel addition, and optional duration/distance estimates remain visible to the rider before acceptance. Set `dispatch_enabled` or use the admin dispatch action to offer it.

The dispatcher picks an active rider account that has no accepted/running session or other pending offer, preferring higher scores and older offer times. Riders do not need to go online. The legacy `is_available` field and availability endpoint are retained for older clients but do not gate, withdraw, or prevent acceptance of offers. An ignored deadline expires; a declined offer closes; the session passes to another eligible rider using that rider's own window. Exhausted candidates are revisited after five minutes. Only the addressed rider may accept, and acceptance binds the shared orders to that rider atomically.

Managers may include `offer_to_rider` with the shared rider user ID in session creation to address the initial offer explicitly. Assignment still occurs only after acceptance; busy/inactive riders are rejected. Subsequent handoff uses the dispatcher when dispatch is enabled.

After acceptance, confirm pickup to start the run. Stops cluster coordinates to six decimal places and sort by distance from the vendor, nearest first. The map shows the pickup and numbered clusters. External Google Maps navigation provides driving directions; the overview polyline shows stop sequence, not a turn-by-turn road route.

The rider submits the customer's four-digit PIN or QR payload for each order. Verification secrets are not included in the new rider responses. Delivery updates the shared order and attempt together; the last resolved order closes the session. A failed delivery retains its reason and timestamp and releases the shared order for operations to schedule a later attempt. Its original session remains valid history when a different rider accepts that later run. Confirm actual finances through the completed session's admin action after reviewing the recorded amounts; the UI distinguishes estimates from confirmed earnings.

Cancellation records the rider, timestamp, reason, and stage, releases remaining orders, and creates a replacement session for another rider. Replacement amounts are estimates prorated from the original quote using remaining delivery fees (or remaining order count when fees are zero). Already delivered orders stay in their original run and cannot be verified twice. Cancelled runs retain draft quotes; any payout adjustment for a partially completed cancellation belongs to the later settlement policy.

## APIs

All rider endpoints require JWT authentication and `user.role == rider`; admin endpoints require platform admin access.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET / PATCH | `/api/riders/profile/` | Rider profile and editable account details |
| POST | `/api/riders/availability/` | Legacy flag only; does not control job offers |
| GET | `/api/riders/dashboard/?period=week` | Today/week/month/lifetime statistics, profile, earnings series, recent events |
| GET | `/api/riders/jobs/` | Pending addressed offers and server time |
| POST | `/api/riders/jobs/<offer>/accept/` | Accept before the deadline |
| POST | `/api/riders/jobs/<offer>/decline/` | Decline an offer |
| GET | `/api/riders/sessions/` | Current and past sessions |
| GET | `/api/riders/sessions/<session>/` | Single-vendor run and ordered stop clusters |
| POST | `/api/riders/sessions/<session>/pickup/` | Confirm the pickup |
| POST | `/api/riders/sessions/<session>/orders/<order>/verify/` | Submit `code` and optional actual `distance_km` |
| POST | `/api/riders/sessions/<session>/orders/<order>/failed/` | Record a delivery issue with `reason` |
| POST | `/api/riders/sessions/<session>/cancel/` | Record a cancellation reason/details and release remaining orders |
| GET | `/api/riders/history/cancellations/` | Up to 100 recent cancellation events |
| GET | `/api/riders/history/penalties/` | Up to 100 recent penalties |
| GET / POST | `/api/riders/admin/sessions/` | Inspect/create single-vendor jobs |
| POST | `/api/riders/admin/riders/<rider>/score/` | External score, version, and acceptance-window update |
| POST | `/api/riders/admin/riders/<rider>/penalties/` | Idempotent penalty issue |

Example session creation:

```json
{
  "vendor": 2,
  "orders": [1048, 1049, 1050],
  "rider_cut": "420.00",
  "service_charge": "80.00",
  "fuel_surge_addition": "35.00",
  "estimated_minutes": 32,
  "estimated_distance": "5.400",
  "dispatch_enabled": true
}
```

## Deployment

Sync the backend source and `riders` migrations, then run `python manage.py migrate` before starting the updated application. Docker Compose includes a `rider_dispatcher` worker that waits for the web application to be healthy and processes expiration every two seconds. Start both services:

```sh
docker compose up -d --build web rider_dispatcher
```

Without Docker, run `python manage.py run_rider_dispatcher --interval 2` as a supervised worker in the same environment. `--once` performs one dispatch pass. The request paths also perform opportunistic dispatch, but the worker is required for handover while apps are closed.

Legacy sessions with only one vendor in their existing order links are assigned that vendor by migration `0004`. Mixed-vendor historical sessions remain unbound for review; they are not new offer candidates. New model/API session creation rejects mixed vendors.

## Android app

The Android implementation is rebuilt in `C:/Users/Saranga/Documents/GameKadeRider` with Kotlin namespace `com.onemind.mealrider` and visible name **Meal Rider**. The installed application ID is retained for compatibility with the existing Android Google Maps key restriction and debug signing identity. The old source is backed up under `.design/previous-rider-app` and is excluded from app compilation.

The app defaults to `https://mealdrop.shop/`, uses the new rider endpoints, and handles authentication through the shared OTP/JWT APIs. Deploy the backend before distributing the rebuilt APK.

Debug-only review intents provide labelled synthetic activity without logging into or changing a server account. Review PIN `4821` and QR payload `2611024821` work only in that in-memory review repository. Production handover always calls the authenticated backend.

The four tabs are Dashboard, Jobs, Sessions, and Account. Screens borrow DoorDash Dasher's hierarchy and pickup/delivery progression and inDrive's map-led offer structure, adapted to Android Material navigation, sheets, insets, larger type, and dark theme.

## Development rider data

`python manage.py seed_rider_demo --phone 0721992125` creates labelled sample history for an existing active rider: seven completed sessions (35 deliveries), two cancellation records, two idempotent penalty records, and a private five-order job. The dummy kitchen, product and customer accounts stay inactive; the private job has dispatch disabled so it cannot pass to other riders. Existing name, vehicle registration and emergency contact remain unchanged. If score is missing, the sample sets score 92.40 and a 300-second acceptance window, labelled `demo-v1`. Demo orders use PIN `4821`.

Repeating the command does not duplicate history/counters. `--refresh-offer` renews only an expired private demo offer when the rider is not busy. Run it after syncing these changes if you need another timed test. This command adds development records to the configured database and sends no SMS or notifications.

## Version 2.2 order and admin contract

Orders have no two-word identity. `delivery_code` remains ten numeric digits (Colombo `YYMMDD` plus a random four-digit PIN). Customer responses expose the four-digit `delivery_pin` and numeric QR payload. Rider manual verification accepts four digits and resolves today?s prefix; an older order date or wrong PIN returns `Order not found for today.` Legacy UUID QR codes are rejected. Migration `orders.0008` removes the order-name and legacy UUID columns while retaining orders and numeric codes. Session orders return a plain `number`, `order_value`, `delivery_fee`, and `total_amount` (which already includes the fee).

Stops group by saved location ID, carry `is_system_defined` and `unique_identity`, and return the system name for public locations or two-word identity for private locations. Description remains available. Migration `orders.0007` repairs malformed private location identities; `0008` removes the previously added order-name field.

The Django admin session list has rider/status/finance controls and a Save action per row. Assignment synchronizes queued orders, pickup supplies start timestamps/order status, cancellation releases/reassigns remaining work, completion requires resolved attempts, and financial confirmation requires a completed session. Confirmed facts stay locked.

The production database connection, API formatting, and admin timezone use `Asia/Colombo` (+05:30). Existing recorded instants are preserved. Slide gestures guard acceptance, pickup, and travel starts in the Android application.

## Login OTP 2.2.2

`OTP_SMS_BYPASS_NUMBERS` configures 0721992125 and 0774610536. These accounts generate/save a normal code and return `delivery_method=admin`, `otp_length=6`, without calling Text.lk. Codes remain private to the backend admin. Account roles are preserved. Normal numbers retain SMS. Public send/verify endpoints ignore stale JWTs and normalize Sri Lankan phone formats. No schema change is needed.
