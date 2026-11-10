# Create delivery sessions from the order map

The planner is at `/dispatch/plan/`. Log in with a platform admin's phone number
and password. Django admin has a **Session map** link in its header, the Session
**Add** button opens the planner, and the existing dispatch dashboard links to
**Plan a session on a map**.

## Create a session and offer it

1. Choose today's delivery day and the kitchen. Every session contains one kitchen's orders.
2. Select individual orders in a map pin's popup or in the list. **Select an area**
   lets you drag a rectangle around several delivery pins; it also supports touch.
   Orders at one saved address share a stop. Search and **Available only** filter the map and list.
3. Review the selected order and stop counts. The default rider cut is the combined
   delivery fees. Edit it if needed, and expand **Platform and fuel amounts** to
   enter the platform share or fuel addition. These amounts are draft quotes.
4. Choose a rider by phone number in **Rider or dispatch option**. The list shows
   their acceptance window and explains unavailable riders. You can instead choose
   **Automatic rider offers** or **Save as planned**.
5. Click **Create session & offer**, **Create session & dispatch**, or
   **Create planned session**, according to the selected option.

The result appears in **Sessions** with its order count, stops, quote, and offer.
Use **View stops on map** to inspect the run. The lines show the stop sequence;
the rider opens driving directions in their app.

## Offers and rider acceptance

Job Offers in Django admin are intentionally read-only. An offer is generated
from a planned session using the rider's configured 5–600 second acceptance
window. Manually inserting an offer would bypass the checks for an active,
complete rider profile, an available rider, reserved orders, and the deadline.

The rider opens **Jobs** in Meal Rider and accepts before the displayed deadline.
Acceptance assigns the session and shared orders to that rider. They then confirm
pickup and verify each delivery with the customer's code. A direct offer stays
with the chosen rider; automatic dispatch can offer the session to another
eligible rider after a decline or expiry.

For an unassigned planned session from today, **Sessions → Send a fresh job offer**
can issue another offer after the previous one expired or declined. It preserves
the old offer and its deadline. A pending live offer is not overwritten, and a
rider can have only one pending offer and one accepted or running session.

## When an order cannot be selected

The map explains reserved orders, completed or cancelled orders, existing rider
assignments, missing addresses, invalid fees, and locations outside the delivery
area. Manage an order already in a session through that session. The legacy
assignment endpoints also prevent overwriting session orders.

The calendar can display earlier days and their sessions. New session creation
and re-offering in this planner use today because the rider verification API
accepts today's delivery codes.

## How the earlier example was made

Orders **104** and **105** already existed. After both belonged to kitchen **2**,
a one-off Django Python script added three labelled sample orders using the
kitchen's existing product and normal delivery pricing:

| Order | Saved delivery location | Delivery fee |
| --- | --- | --- |
| 107 | Eliyakanda Girls Hostel | LKR 55.00 |
| 108 | Eliyakanda New Boy' Hostel | LKR 55.00 |
| 109 | Wellamadama Girls Hostel | LKR 20.00 |

Their sample customer accounts are inactive. The script called the session
service with all five order IDs, creating **Session 18** and five queued
`SessionOrder` links with delivery-fee snapshots. The two original orders shared
one stop, so the run had four stops.

The script created **Offer 3** for **0741798174** using their 600-second window.
Its original deadline was **10 November 2026, 10:41 PM Sri Lanka time**. The rider
still had to accept it. The LKR 210 combined fees became a draft rider quote,
with zero platform share and fuel addition. No finance settlement was recorded.

The new planner calls the same service through normal admin session
authentication and CSRF protection. It groups existing orders; customers place
new orders through the usual checkout.

## Deploy

These changes must be present in the backend server's checkout before the new
page is available on the live site. From that checkout, use the existing deployment:

```sh
docker compose --env-file .env.prod up -d --build
```

The web container collects static files and runs migrations during startup.
This feature adds no database migration. Keep the rider dispatcher running for
automatic offers and deadline processing. Leaflet is bundled locally; base map
tiles come from OpenStreetMap.
