# Basket delivery quotes

Delivery charges now use the number of individual meals, including quantity, for each kitchen separately. For a kitchen at grid distance 4, successive meals use delivery fees from tiers 4, 3, 2, 1, and 0. Tier 0 repeats for further meals.

The minimum is the starting tier's `minimum_delivery_charge_per_parcel` multiplied by that kitchen's meal quantity. The discount is exactly 10% of the meal subtotal, rounded to two decimal places and capped so delivery stays at or above that minimum.

```text
stepped fee = sum of the configured fees for each parcel
minimum = starting tier minimum per parcel × parcel count
base delivery = max(stepped fee, minimum)
delivery discount = min(meal subtotal × 10%, base delivery − minimum)
delivery fee = base delivery − delivery discount
order total = meal subtotal + delivery fee
```

The quote and order-creation APIs share this calculation. Product IDs, quantities, and a selected delivery location are sufficient: product prices and H3 distance are always derived by the server. Client-supplied subtotal or tier hints do not control prices.

## Deploy and configure

Upload the changed backend source and migrations, then run `python manage.py migrate` in the deployment environment and restart the backend. The new migration is `multivender/0006_minimum_delivery_charge_per_parcel`; include earlier pending migrations from this workspace too.

In Django admin, open **Delivery distance tiers** and set `minimum_delivery_charge_per_parcel` for every tier you serve, including tier 0. Existing rows start with this value unset. This is intentional: quotes return an explicit configuration error until a real minimum is provided. No minimum prices are invented. A configured zero is valid if free delivery at the cap is intended. Each minimum must be at least zero and no greater than that tier's delivery fee.

## Quote API

`POST /shop/orders/delivery-quote/`

```json
{
  "location": 43,
  "orders": [
    {"vendor": 1, "items": [{"product": 7, "quantity": 2}]}
  ]
}
```

Authenticated customers can use their own saved location or a system location. Anonymous browsing can supply `lat` and `lng` instead of a saved location ID. Coordinate quotes are read-only and disclose no saved-address details.

The response includes aggregate `subtotal`, `delivery_fee`, `delivery_discount`, `total_amount`, and `can_checkout`. Each entry in `quotes` contains the kitchen's actual product data, parcel count, stepped charges, discount cap, discount applied, minimum, final delivery fee, and fee per parcel.

`next_saving` selects an in-stock addition from that kitchen's actual menu and returns its product, `additional_spend`, `delivery_fee_after`, and saving amount. `saving_kind` distinguishes `total` from `per_parcel`: adding a second meal may increase total delivery while lowering delivery per meal. The UI must label that distinction. `minimum_extra_spend_for_lower_total` also reports the exact extra price required for one further parcel to reduce total delivery, accounting for the new parcel's charge and currency rounding. It is null when that cannot happen under the minimum.

Errors use a `code` and `detail`. Missing minimums or stepped tiers return HTTP 422. Unavailable quantities can still be priced but return `can_checkout: false` and issues to resolve.

When placing each kitchen's order, send the quoted `total_amount` as `expected_total_amount` to `/shop/orders/create/`. If server prices or delivery charges have changed, order creation rejects the mismatch rather than silently charging the new amount.
