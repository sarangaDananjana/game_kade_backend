# Rider data models

This app contains delivery-run records and rider dashboard data. [MEAL_RIDER_API.md](../MEAL_RIDER_API.md) documents the rider APIs, timed job offers, admin controls, single-vendor session flow, and rebuilt mobile UI. Payout rates, automatic scoring, and points-award policies remain separate from recorded facts.

## Session

One `Session` is a single delivery run for one vendor. Its `orders` relation uses `SessionOrder` so the kitchen's orders can be clustered by destination, and failed attempts can be retained when an order moves to a later run.

| Field | Meaning |
| --- | --- |
| `id`, `date` | Run ID and scheduled delivery date; the default date uses Asia/Colombo |
| `assigned_rider` | Shared `users.CustomUser` with role `rider` |
| `rider_reference` | Original rider ID retained if the user account is deleted |
| `orders` | Existing shared orders; their customer, location, vendor, and items remain accessible |
| `status` | `planned`, `accepted`, `in_progress`, `completed`, or `cancelled` |
| `rider_cut` | Base rider earnings in LKR, entered as a money amount |
| `service_charge` | Platform's share of delivery revenue in LKR |
| `fuel_surge_addition` | Extra rider earnings for fuel in LKR |
| `rider_revenue` | Database-generated `rider_cut + fuel_surge_addition` |
| `total_revenue` | Database-generated `rider_cut + service_charge + fuel_surge_addition`; food subtotal is excluded |
| `finance_status`, `settled_at` | Whether the revenue split is confirmed and when; this does not record a bank payout |
| `session_distance` | Actual run distance in kilometres, with metre precision |
| `started_at`, `finished_at` | Actual run timestamps |
| `session_time` | Database-generated duration between start and finish; null before both are recorded |
| `points_awarded` | Points credited for the completed run; zero until an award is recorded |
| `notes`, `created_at`, `updated_at` | Operational notes and record timestamps |

Money amounts and distance cannot be negative. A rider can have several planned runs but only one accepted or in-progress run. Runs cannot close while orders remain queued. A started run keeps its rider, and confirmed financial/run facts cannot be overwritten through model saves.

## SessionOrder

This link records one order's delivery attempt within a run. It stores the existing order FK, original order ID, delivery-fee snapshot, route sequence, assignment time, optional delivery deadline, finish time, and failure reason.

Attempt statuses are `queued`, `delivered`, `failed`, and `removed`. An order has at most one queued or delivered attempt across runs, which prevents overlapping assignments and double-counting successful delivery. A failed or removed attempt releases it for a later session; the old attempt remains as history.

Create links through `SessionOrder.objects.create(session=session, order=order, ...)`, rather than the generic `session.orders.add()` method. Model saves populate the order ID and fee snapshots and validate the rider, status, and timestamps. Completed attempt history and fee snapshots remain when a source order is deleted; the order FK becomes null.

## RiderInfo and dashboard statistics

`RiderInfo.user` is one-to-one with a rider user. Migration `0002_existing_rider_profiles` creates profiles for existing riders; a user-save signal creates one when a new or existing user receives the `rider` role. The legacy availability flag does not control new job offers. Active rider accounts receive addressed offers and can accept them before expiry.

Profile completion is computed from saved name, vehicle details, and emergency contact; a bicycle does not require a registration number. The API returns task rows and completed/remaining totals. These counts are derived rather than stored, and vehicle make/model and colour are required, and incomplete profiles cannot receive or accept new jobs.

Dashboard numbers are calculated from session and attempt records. Weekly/monthly counters are not stored on the profile, so there are no resets or duplicate totals to synchronize.

```python
from datetime import date

profile = rider_user.rider_info
lifetime = profile.stats()
this_week = profile.weekly_stats()
this_month = profile.monthly_stats()
historical_week = profile.weekly_stats(reference_date=date(2026, 11, 2))
custom_period = profile.stats(start_date=date(2026, 11, 1), end_date=date(2026, 12, 1))
```

Use one statistics dictionary for a dashboard. Convenience properties `rider_points`, `rider_revenue`, `rider_performance`, and `total_orders` read lifetime totals; reading them individually runs separate reports.

Reports include completed sessions, delivered/failed orders, delivery attempts, points, rider and platform revenue, fuel additions, total distance, total/average run time, average speed, completion rate, and on-time rate. `total_orders` means successfully delivered orders; attempts and failures are reported separately.

- Delivery attempts use their actual `finished_at` timestamps. Completion rate is successful attempts divided by successful plus failed attempts.
- Run counts, distance, time, and points use a completed run's `finished_at` timestamp.
- Confirmed revenue uses `settled_at`, so a run completed in October but confirmed in November credits its revenue in November.
- Weeks run Monday to Monday and months follow the calendar in Asia/Colombo. Start dates are inclusive; end dates are exclusive.
- On-time rate uses only delivered orders with a recorded delivery deadline. Rates without evidence return null. Completed runs with unconfirmed finances are reported in `unconfirmed_revenue_sessions`; draft amounts do not inflate confirmed revenue.

## Order integration

The rider services update the existing `Order.rider` and session membership in a transaction. The new handover API updates the order and its attempt outcome together, recording the real finish timestamp and any failure/deadline data.

Confirm finances after completing a run and recording the actual rider cut, platform share, fuel addition, and settlement timestamp. Rates, points rules, GPS distance collection, payment adjustments, and payouts are not assumed by the models.

Apply the app migrations in the deployment environment with `python manage.py migrate`. Migration `0005` adds vehicle make/model and colour; `orders.0007` assigns stable readable order names and repairs legacy private location names. Existing orders, customers, kitchens, and delivery zones remain in place.
