from decimal import Decimal
from unittest.mock import patch

import h3
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from .models import DeliveryDistanceTier, Order, OrderLocation, Product, Vendor
from .serializers import VendorSerializer
from .services.pricing import (
    DeliveryPricingError, load_delivery_tiers, minimum_spend_for_lower_total,
    next_delivery_saving, stepped_delivery_quote,
)
from .tests import ORDER_TIME, OrderFixtures


class PricingFixtures(OrderFixtures):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for distance, fee in enumerate([20, 40, 80, 120, 160]):
            DeliveryDistanceTier.objects.create(grid_distance=distance, delivery_fee=fee,
                minimum_delivery_charge_per_parcel=40 if distance == 4 else 10)
        cell = h3.grid_ring(cls.vendor.h3_index, 4)[0]
        lat, lng = h3.cell_to_latlng(cell)
        cls.far_location = OrderLocation.objects.create(name='Tier four', lat=lat, lng=lng,
            unique_identity='Test entrance', is_system_defined=True)
        cls.product.price = Decimal('200.00')
        cls.product.save(update_fields=['price'])

    def quote(self, subtotal, count):
        return stepped_delivery_quote(4, subtotal, count, load_delivery_tiers())


class DeliveryPricingTests(PricingFixtures):
    def test_single_meal_matches_the_requested_example(self):
        quote = self.quote(200, 1)
        self.assertEqual(quote['base_delivery_fee'], '160.00')
        self.assertEqual(quote['delivery_discount'], '20.00')
        self.assertEqual(quote['delivery_fee'], '140.00')
        self.assertEqual(quote['total_amount'], '340.00')

    def test_second_meal_uses_the_next_tier_and_lowers_the_average(self):
        quote = self.quote(400, 2)
        self.assertEqual([row['grid_distance'] for row in quote['parcel_breakdown']], [4, 3])
        self.assertEqual(quote['base_delivery_fee'], '280.00')
        self.assertEqual(quote['delivery_discount'], '40.00')
        self.assertEqual(quote['delivery_fee'], '240.00')
        self.assertEqual(quote['fee_per_parcel'], '120.00')
        self.assertEqual(quote['total_amount'], '640.00')

    def test_minimum_uses_the_starting_tier_for_every_parcel(self):
        quote = self.quote(5000, 3)
        self.assertEqual(quote['minimum_delivery_fee'], '120.00')
        self.assertEqual(quote['delivery_fee'], '120.00')
        self.assertEqual(quote['delivery_discount'], '240.00')
        self.assertTrue(quote['minimum_reached'])

    def test_tier_zero_repeats_for_all_remaining_parcels(self):
        quote = self.quote(1200, 6)
        self.assertEqual(quote['stepped_delivery_fee'], '440.00')
        self.assertEqual(quote['parcel_breakdown'][-1]['grid_distance'], 0)
        self.assertEqual(quote['parcel_breakdown'][-1]['parcel_count'], 2)

    def test_minimum_still_applies_when_it_exceeds_the_stepped_sum(self):
        quote = self.quote(20000, 100)
        self.assertEqual(quote['stepped_delivery_fee'], '2320.00')
        self.assertEqual(quote['delivery_fee'], '4000.00')
        self.assertEqual(quote['delivery_discount'], '0.00')

    def test_ten_percent_uses_paise_precision_instead_of_whole_hundreds(self):
        self.assertEqual(self.quote('199.99', 1)['delivery_discount'], '20.00')
        self.assertEqual(self.quote('150.50', 1)['delivery_discount'], '15.05')

    def test_missing_minimum_never_invents_a_fee(self):
        DeliveryDistanceTier.objects.filter(grid_distance=4).update(minimum_delivery_charge_per_parcel=None)
        with self.assertRaises(DeliveryPricingError) as failure:
            self.quote(200, 1)
        self.assertEqual(failure.exception.code, 'minimum_not_configured')

    def test_missing_intermediate_tier_cannot_be_skipped(self):
        DeliveryDistanceTier.objects.filter(grid_distance=3).delete()
        with self.assertRaises(DeliveryPricingError):
            self.quote(400, 2)

    def test_admin_rejects_a_minimum_higher_than_the_fee(self):
        tier = DeliveryDistanceTier(grid_distance=4, delivery_fee=160, minimum_delivery_charge_per_parcel=170)
        with self.assertRaises(ValidationError):
            tier.clean()

    def test_spend_threshold_includes_the_added_parcels_fee_and_rounding(self):
        quote = self.quote(200, 1)
        threshold = Decimal(minimum_spend_for_lower_total(quote, load_delivery_tiers()))
        self.assertEqual(threshold, Decimal('1200.05'))
        self.assertLess(Decimal(self.quote(Decimal('200.00') + threshold, 2)['delivery_fee']), Decimal('140.00'))
        self.assertEqual(self.quote(Decimal('200.00') + threshold - Decimal('.01'), 2)['delivery_fee'], '140.00')

    def test_real_addition_is_labelled_as_average_saving_when_total_goes_up(self):
        suggestion = next_delivery_saving(self.quote(200, 1), [self.product], {self.product.pk: 1}, load_delivery_tiers())
        self.assertEqual(suggestion['product'], self.product)
        self.assertEqual(suggestion['additional_spend'], '200.00')
        self.assertEqual(suggestion['saving_kind'], 'per_parcel')
        self.assertEqual(suggestion['per_parcel_saving'], '20.00')
        self.assertEqual(suggestion['total_delivery_saving'], '0.00')
        self.assertEqual(suggestion['delivery_fee_after'], '240.00')

    def test_total_saving_is_only_shown_for_an_actual_qualifying_menu_item(self):
        product = Product.objects.create(vendor=self.vendor, name='Larger meal', price=1400, stock_quantity=5)
        suggestion = next_delivery_saving(self.quote(200, 1), [product], {}, load_delivery_tiers())
        self.assertEqual(suggestion['saving_kind'], 'total')
        self.assertEqual(suggestion['total_delivery_saving'], '20.00')

    def test_sold_out_items_are_never_recommended(self):
        self.assertIsNone(next_delivery_saving(self.quote(200, 1), [self.product], {self.product.pk: 100}, load_delivery_tiers()))

    def test_minimum_reached_has_no_extra_saving_claim(self):
        self.assertIsNone(next_delivery_saving(self.quote(5000, 3), [self.product], {}, load_delivery_tiers()))


class BasketQuoteApiTests(PricingFixtures):
    def setUp(self):
        self.client = APIClient()

    def payload(self, quantity=1, location=True):
        data = {'orders': [{'vendor': self.vendor.pk, 'items': [{'product': self.product.pk, 'quantity': quantity}]}]}
        if location:
            data['location'] = self.far_location.pk
        else:
            data.update(lat=self.far_location.lat, lng=self.far_location.lng)
        return data

    def request_quote(self, data=None):
        return self.client.post('/shop/orders/delivery-quote/', data or self.payload(), format='json')

    def test_quote_uses_database_prices_and_h3_instead_of_client_hints(self):
        data = self.payload()
        data.update(subtotal='1.00', grid_distance=0, delivery_fee='0.00')
        data['orders'][0].update(subtotal='1.00', grid_distance=0)
        response = self.request_quote(data)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['total_amount'], '340.00')
        self.assertEqual(response.data['quotes'][0]['grid_distance'], 4)
        self.assertEqual(response.data['quotes'][0]['next_saving']['product']['price'], '200.00')

    def test_coordinate_quote_works_without_exposing_saved_addresses(self):
        response = self.request_quote(self.payload(location=False))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['delivery_fee'], '140.00')

    def test_each_quantity_is_a_parcel_including_duplicate_lines(self):
        data = self.payload()
        data['orders'][0]['items'].append(data['orders'][0]['items'][0].copy())
        response = self.request_quote(data)
        self.assertEqual(response.data['delivery_fee'], '240.00')
        self.assertEqual(response.data['quotes'][0]['parcel_count'], 2)

    def test_kitchens_have_independent_tier_sequences(self):
        vendor = Vendor.objects.create(owner=self.owner, shop_name='Second kitchen', lat=self.vendor.lat, lng=self.vendor.lng)
        product = Product.objects.create(vendor=vendor, name='Second meal', price=300, stock_quantity=5)
        data = self.payload()
        data['orders'].append({'vendor': vendor.pk, 'items': [{'product': product.pk, 'quantity': 1}]})
        response = self.request_quote(data)
        self.assertEqual(response.data['delivery_fee'], '270.00')
        self.assertEqual(response.data['total_amount'], '770.00')

    def test_pickup_needs_no_address_or_delivery_configuration(self):
        self.vendor.is_pickup_only = True
        self.vendor.save(update_fields=['is_pickup_only'])
        DeliveryDistanceTier.objects.all().delete()
        data = self.payload()
        data.pop('location')
        response = self.request_quote(data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['delivery_fee'], '0.00')
        self.assertEqual(response.data['total_amount'], '200.00')

    def test_unknown_minimum_returns_an_explicit_configuration_error(self):
        DeliveryDistanceTier.objects.filter(grid_distance=4).update(minimum_delivery_charge_per_parcel=None)
        response = self.request_quote()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.data['code'], 'minimum_not_configured')

    def test_another_customers_saved_location_is_not_quotable(self):
        data = self.payload()
        data['location'] = self.location.pk
        self.assertEqual(self.request_quote(data).status_code, 403)
        self.client.force_authenticate(user=self.other_rider)
        self.assertEqual(self.request_quote(data).status_code, 403)

    def test_wrong_shop_product_and_invalid_quantities_are_rejected(self):
        for quantity in [0, -1]:
            self.assertEqual(self.request_quote(self.payload(quantity)).status_code, 400)
        vendor = Vendor.objects.create(owner=self.owner, shop_name='Other', lat=self.vendor.lat, lng=self.vendor.lng)
        data = self.payload()
        data['orders'][0]['vendor'] = vendor.pk
        self.assertEqual(self.request_quote(data).status_code, 400)

    def test_unavailable_stock_still_quotes_but_blocks_checkout(self):
        self.product.stock_quantity = 1
        self.product.save(update_fields=['stock_quantity'])
        response = self.request_quote(self.payload(2))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['can_checkout'])
        self.assertTrue(response.data['quotes'][0]['issues'])

    def test_configured_grid_four_vendor_is_available(self):
        self.assertTrue(VendorSerializer(self.vendor, context={'order_location': self.far_location}).data['is_available'])

    def test_checkout_uses_the_same_fee_as_the_quote(self):
        self.client.force_authenticate(user=self.customer)
        data = {'vendor': self.vendor.pk, 'location': self.far_location.pk,
                'items': [{'product': self.product.pk, 'quantity': 2}], 'expected_total_amount': '640.00'}
        with patch('multivender.views.timezone.now', return_value=ORDER_TIME):
            response = self.client.post('/shop/orders/create/', data, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['delivery_fee'], '240.00')
        self.assertEqual(response.data['total_amount'], '640.00')

    def test_changed_price_cannot_silently_charge_more_than_the_displayed_quote(self):
        self.client.force_authenticate(user=self.customer)
        self.product.price = Decimal('250.00')
        self.product.save(update_fields=['price'])
        data = {'vendor': self.vendor.pk, 'location': self.far_location.pk,
                'items': [{'product': self.product.pk, 'quantity': 1}], 'expected_total_amount': '340.00'}
        with patch('multivender.views.timezone.now', return_value=ORDER_TIME):
            response = self.client.post('/shop/orders/create/', data, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('expected_total_amount', response.data)
        self.assertFalse(Order.objects.exists())
