import h3
from multivender.models import DeliveryDistanceTier

def calculate_delivery_fee(vendor, order_location, subtotal=0):
    """
    Calculates delivery fee dynamically based on the H3 grid distance
    between the Vendor and the OrderLocation.
    Applies a discount of Rs 10 for every Rs 100 in the subtotal.
    """
    if not vendor.h3_index or not order_location.h3_index:
        raise ValueError("Missing H3 index on vendor or order location.")
        
    try:
        # Calculate grid distance (number of hexagons apart)
        distance = h3.grid_distance(vendor.h3_index, order_location.h3_index)
    except h3.H3Error as e:
        raise ValueError(f"H3 spatial error: {str(e)}")
        
    try:
        tier = DeliveryDistanceTier.objects.get(grid_distance=distance)
        base_fee = tier.delivery_fee
        
        # Apply discount: Rs 10 off for every Rs 100 in subtotal
        discount = int(subtotal // 100) * 10
        discounted_fee = max(0, float(base_fee) - discount)
        
        return discounted_fee
    except DeliveryDistanceTier.DoesNotExist:
        raise ValueError(f"Out of delivery zone. No rate defined for distance {distance}.")
