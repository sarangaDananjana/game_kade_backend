"""Authoritative stepped parcel pricing for both quotes and placed orders."""

from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

import h3
from orders.service_area import DeliveryAreaError, check_delivery_area

from orders.models import DeliveryDistanceTier


MONEY_UNIT = Decimal('0.01')
DISCOUNT_RATE = Decimal('0.10')


class DeliveryPricingError(ValueError):
    def __init__(self, message, code='delivery_unavailable'):
        super().__init__(message)
        self.code = code


def money(value):
    return Decimal(str(value)).quantize(MONEY_UNIT, rounding=ROUND_HALF_UP)


def money_text(value):
    return format(money(value), '.2f')


def load_delivery_tiers():
    return {tier.grid_distance: tier for tier in DeliveryDistanceTier.objects.all()}


def delivery_grid_distance(vendor, order_location=None, coordinates=None):
    if vendor.is_pickup_only:
        return None
    if order_location is None and coordinates is None:
        raise DeliveryPricingError('Choose a delivery address to calculate delivery.', 'location_required')
    lat, lng = (order_location.lat, order_location.lng) if order_location else coordinates
    try:
        check_delivery_area(lat, lng)
    except DeliveryAreaError as exc:
        raise DeliveryPricingError(str(exc), exc.code) from exc
    try:
        vendor_cell = vendor.h3_index or h3.latlng_to_cell(vendor.lat, vendor.lng, 8)
        location_cell = (order_location.h3_index or h3.latlng_to_cell(order_location.lat, order_location.lng, 8)) if order_location else h3.latlng_to_cell(*coordinates, 8)
        return h3.grid_distance(vendor_cell, location_cell)
    except (h3.H3BaseException, TypeError, ValueError) as exc:
        raise DeliveryPricingError('Delivery could not be calculated for this address.', 'invalid_delivery_location') from exc


def stepped_delivery_quote(grid_distance, subtotal, parcel_count, tiers):
    if not isinstance(parcel_count, int) or parcel_count < 1:
        raise DeliveryPricingError('At least one meal is required.', 'invalid_parcel_count')
    subtotal = money(subtotal)
    if not subtotal.is_finite() or subtotal < 0:
        raise DeliveryPricingError('The meal subtotal must be a valid amount.', 'invalid_subtotal')
    starting_tier = tiers.get(grid_distance)
    if starting_tier is None:
        raise DeliveryPricingError('Delivery is not available to this address.', 'out_of_delivery_zone')
    minimum = starting_tier.minimum_delivery_charge_per_parcel
    if minimum is None:
        raise DeliveryPricingError('Delivery pricing for this kitchen is not ready yet.', 'minimum_not_configured')
    minimum = money(minimum)
    if minimum < 0 or minimum > starting_tier.delivery_fee:
        raise DeliveryPricingError('Delivery pricing for this kitchen needs to be updated.', 'invalid_tier_configuration')

    stepped_fee = Decimal('0.00')
    breakdown = []
    for offset in range(min(parcel_count, grid_distance + 1)):
        distance = grid_distance - offset
        tier = tiers.get(distance)
        if tier is None or tier.delivery_fee < 0:
            raise DeliveryPricingError('Delivery pricing for this basket is not ready yet.', 'missing_parcel_tier')
        count = parcel_count - grid_distance if distance == 0 else 1
        charge = money(tier.delivery_fee) * count
        stepped_fee += charge
        breakdown.append({'grid_distance': distance, 'parcel_count': count,
                          'fee_per_parcel': money_text(tier.delivery_fee), 'delivery_fee': money_text(charge)})

    floor = money(minimum * parcel_count)
    base = max(money(stepped_fee), floor)
    discount_cap = money(base - floor)
    requested_discount = money(subtotal * DISCOUNT_RATE)
    discount = min(requested_discount, discount_cap)
    fee = money(base - discount)
    return {
        'grid_distance': grid_distance, 'parcel_count': parcel_count,
        'subtotal': money_text(subtotal), 'stepped_delivery_fee': money_text(stepped_fee),
        'base_delivery_fee': money_text(base), 'minimum_per_parcel': money_text(minimum),
        'minimum_delivery_fee': money_text(floor), 'discount_rate': str(DISCOUNT_RATE),
        'discount_cap': money_text(discount_cap), 'delivery_discount': money_text(discount),
        'delivery_fee': money_text(fee), 'fee_per_parcel': money_text(fee / parcel_count),
        'total_amount': money_text(subtotal + fee), 'minimum_reached': fee == floor,
        'parcel_breakdown': breakdown,
    }


def quote_delivery(vendor, order_location=None, subtotal=0, parcel_count=1, *, coordinates=None, tiers=None):
    if vendor.is_pickup_only:
        return {
            'grid_distance': None, 'parcel_count': parcel_count, 'subtotal': money_text(subtotal),
            'stepped_delivery_fee': '0.00', 'base_delivery_fee': '0.00', 'minimum_per_parcel': '0.00',
            'minimum_delivery_fee': '0.00', 'discount_rate': str(DISCOUNT_RATE), 'discount_cap': '0.00',
            'delivery_discount': '0.00', 'delivery_fee': '0.00', 'fee_per_parcel': '0.00',
            'total_amount': money_text(subtotal), 'minimum_reached': True, 'parcel_breakdown': [],
        }
    distance = delivery_grid_distance(vendor, order_location, coordinates)
    return stepped_delivery_quote(distance, subtotal, parcel_count, tiers if tiers is not None else load_delivery_tiers())


def calculate_delivery_fee(vendor, order_location, subtotal=0, parcel_count=1):
    return Decimal(quote_delivery(vendor, order_location, subtotal, parcel_count)['delivery_fee'])


def minimum_spend_for_lower_total(quote, tiers):
    if quote['grid_distance'] is None:
        return None
    try:
        next_quote = stepped_delivery_quote(quote['grid_distance'], 0, quote['parcel_count'] + 1, tiers)
    except DeliveryPricingError:
        return None
    fee = Decimal(quote['delivery_fee'])
    if Decimal(next_quote['minimum_delivery_fee']) >= fee:
        return None
    target_discount = Decimal(next_quote['base_delivery_fee']) - fee + MONEY_UNIT
    threshold_subtotal = ((target_discount - MONEY_UNIT / 2) / DISCOUNT_RATE).quantize(MONEY_UNIT, rounding=ROUND_CEILING)
    return money_text(max(Decimal('0.00'), threshold_subtotal - Decimal(quote['subtotal'])))


def next_delivery_saving(quote, products, quantities, tiers):
    """Use actual menu additions; distinguish total savings from a lower average."""
    distance = quote['grid_distance']
    if distance is None or quote['minimum_reached']:
        return None
    count = quote['parcel_count']
    current_fee = Decimal(quote['delivery_fee'])
    current_average = Decimal(quote['fee_per_parcel'])
    subtotal = Decimal(quote['subtotal'])
    candidates = []
    for product in sorted(products, key=lambda item: (item.price, item.pk)):
        if not product.is_available or product.stock_quantity <= quantities.get(product.pk, 0) or product.price <= 0:
            continue
        try:
            after = stepped_delivery_quote(distance, subtotal + product.price, count + 1, tiers)
        except DeliveryPricingError:
            continue
        total_saving = money(current_fee - Decimal(after['delivery_fee']))
        average_saving = money(current_average - Decimal(after['fee_per_parcel']))
        if total_saving >= MONEY_UNIT or average_saving >= MONEY_UNIT:
            candidates.append((product, after, total_saving, average_saving))
    if not candidates:
        return None
    product, after, total_saving, average_saving = next((candidate for candidate in candidates if candidate[2] >= MONEY_UNIT), candidates[0])
    return {
        'product': product, 'quantity': 1, 'additional_spend': money_text(product.price),
        'saving_kind': 'total' if total_saving > 0 else 'per_parcel',
        'total_delivery_saving': money_text(max(total_saving, 0)),
        'per_parcel_saving': money_text(max(average_saving, 0)),
        'delivery_fee_after': after['delivery_fee'], 'fee_per_parcel_after': after['fee_per_parcel'],
        'parcel_count_after': count + 1,
    }
