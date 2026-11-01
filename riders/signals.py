from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import RiderInfo


@receiver(post_save, sender=get_user_model(), dispatch_uid='riders.ensure_rider_profile')
def ensure_rider_profile(sender, instance, using, raw=False, **kwargs):
    if not raw and instance.role == 'rider':
        profile, _ = RiderInfo.objects.using(using).get_or_create(user_id=instance.pk)
        instance.rider_info = profile
