from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, router, transaction
from django.utils import timezone

from .timeutils import rider_local_date


ZERO = Decimal('0.00')


class RiderInfo(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='rider_info',
        limit_choices_to={'role': 'rider'},
    )
    is_available = models.BooleanField(default=False, help_text='Whether the rider is accepting delivery work.')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        if self.user_id and self.user.role != 'rider':
            raise ValidationError({'user': 'Rider information must belong to a rider user.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def stats(self, start_date=None, end_date=None):
        from .stats import rider_stats
        return rider_stats(self.user_id, start_date, end_date, using=self._state.db or 'default')

    def weekly_stats(self, reference_date=None):
        from .timeutils import week_dates
        return self.stats(*week_dates(reference_date))

    def monthly_stats(self, reference_date=None):
        from .timeutils import month_dates
        return self.stats(*month_dates(reference_date))

    @property
    def rider_points(self):
        return self.stats()['rider_points']

    @property
    def rider_revenue(self):
        return self.stats()['rider_revenue']

    @property
    def rider_performance(self):
        return self.stats()['completion_rate']

    @property
    def total_orders(self):
        return self.stats()['delivered_orders']

    def __str__(self):
        return f'Rider {self.user_id}'


class Session(models.Model):
    class Status(models.TextChoices):
        PLANNED = 'planned', 'Planned'
        IN_PROGRESS = 'in_progress', 'In progress'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'

    class FinanceStatus(models.TextChoices):
        DRAFT = 'draft', 'Not confirmed'
        CONFIRMED = 'confirmed', 'Confirmed'

    date = models.DateField(default=rider_local_date)
    assigned_rider = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='delivery_sessions', limit_choices_to={'role': 'rider'},
    )
    rider_reference = models.PositiveBigIntegerField(editable=False)
    orders = models.ManyToManyField('orders.Order', through='SessionOrder', related_name='delivery_sessions', blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)
    rider_cut = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO,
                                   validators=[MinValueValidator(ZERO)], help_text='Base rider earnings in LKR, not a percentage.')
    service_charge = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO,
                                        validators=[MinValueValidator(ZERO)], help_text='Platform share of delivery revenue in LKR.')
    fuel_surge_addition = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO,
                                             validators=[MinValueValidator(ZERO)], help_text='Additional rider earnings for fuel in LKR.')
    rider_revenue = models.GeneratedField(
        expression=models.F('rider_cut') + models.F('fuel_surge_addition'),
        output_field=models.DecimalField(max_digits=14, decimal_places=2), db_persist=True,
    )
    total_revenue = models.GeneratedField(
        expression=models.F('rider_cut') + models.F('service_charge') + models.F('fuel_surge_addition'),
        output_field=models.DecimalField(max_digits=14, decimal_places=2), db_persist=True,
        help_text='Total delivery revenue in LKR. Meal prices are excluded.',
    )
    finance_status = models.CharField(max_length=20, choices=FinanceStatus.choices, default=FinanceStatus.DRAFT)
    settled_at = models.DateTimeField(null=True, blank=True, help_text='When the delivery revenue split was confirmed; not a bank payout timestamp.')
    session_distance = models.DecimalField(max_digits=10, decimal_places=3, default=Decimal('0.000'),
                                          validators=[MinValueValidator(Decimal('0.000'))], help_text='Actual run distance in kilometres.')
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    session_time = models.GeneratedField(
        expression=models.F('finished_at') - models.F('started_at'),
        output_field=models.DurationField(), db_persist=True, null=True,
        help_text='Actual run duration, derived from its start and finish timestamps.',
    )
    points_awarded = models.PositiveIntegerField(default=0, help_text='Points credited for this completed run; award rules are configured separately.')
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', '-pk']
        indexes = [
            models.Index(fields=['assigned_rider', 'finished_at'], name='riders_run_finish_idx'),
            models.Index(fields=['assigned_rider', 'settled_at'], name='riders_run_settle_idx'),
            models.Index(fields=['assigned_rider', 'date'], name='riders_run_date_idx'),
        ]
        constraints = [
            models.UniqueConstraint(fields=['assigned_rider'], condition=models.Q(status='in_progress'), name='riders_one_active_run'),
            models.CheckConstraint(condition=models.Q(status__in=['planned', 'in_progress', 'completed', 'cancelled']), name='riders_run_valid_status'),
            models.CheckConstraint(condition=models.Q(finance_status__in=['draft', 'confirmed']), name='riders_run_valid_finance'),
            models.CheckConstraint(condition=models.Q(rider_cut__gte=0, service_charge__gte=0, fuel_surge_addition__gte=0, session_distance__gte=0), name='riders_run_nonnegative'),
            models.CheckConstraint(condition=(models.Q(started_at__isnull=True) | models.Q(finished_at__isnull=True) | models.Q(finished_at__gte=models.F('started_at'))), name='riders_run_time_order'),
            models.CheckConstraint(condition=(~models.Q(status='in_progress') | models.Q(started_at__isnull=False, finished_at__isnull=True)), name='riders_active_run_has_start'),
            models.CheckConstraint(condition=(~models.Q(status='completed') | models.Q(started_at__isnull=False, finished_at__isnull=False)), name='riders_completed_run_has_end'),
            models.CheckConstraint(condition=(models.Q(finance_status='draft', settled_at__isnull=True) | models.Q(finance_status='confirmed', status='completed', settled_at__isnull=False)), name='riders_confirmed_finance'),
            models.CheckConstraint(condition=(models.Q(settled_at__isnull=True) | models.Q(finished_at__isnull=False, settled_at__gte=models.F('finished_at'))), name='riders_settlement_after_run'),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self._state.adding and not self.assigned_rider_id:
            errors['assigned_rider'] = 'Choose a rider for this session.'
        if self.assigned_rider_id:
            rider = self.assigned_rider
            if rider.role != 'rider':
                errors['assigned_rider'] = 'Sessions can be assigned only to rider users.'
            elif self.status in [self.Status.PLANNED, self.Status.IN_PROGRESS] and not rider.is_active:
                errors['assigned_rider'] = 'Choose an active rider.'
        if self.started_at and self.finished_at and self.finished_at < self.started_at:
            errors['finished_at'] = 'The finish cannot be before the start.'
        if self.status == self.Status.IN_PROGRESS and (not self.started_at or self.finished_at):
            errors['status'] = 'An active session needs a start timestamp and no finish timestamp.'
        if self.status == self.Status.COMPLETED and (not self.started_at or not self.finished_at):
            errors['status'] = 'A completed session needs start and finish timestamps.'
        if self.pk and self.status in [self.Status.COMPLETED, self.Status.CANCELLED] and self.session_orders.filter(status='queued').exists():
            errors['status'] = 'Resolve or release every queued order before closing the session.'
        if self.pk and self.assigned_rider_id and self.session_orders.exclude(order=None).exclude(order__rider=None).exclude(order__rider_id=self.assigned_rider_id).exists():
            errors['assigned_rider'] = 'The session and its assigned orders must have the same rider.'
        if self.pk and self.finished_at and self.session_orders.filter(finished_at__gt=self.finished_at).exists():
            errors['finished_at'] = 'The session cannot finish before one of its delivery attempts.'
        if self.finance_status == self.FinanceStatus.CONFIRMED:
            if self.status != self.Status.COMPLETED or not self.settled_at:
                errors['finance_status'] = 'Complete the run and provide a settlement timestamp before confirming finances.'
        elif self.settled_at:
            errors['settled_at'] = 'A settlement timestamp requires confirmed finances.'
        if self.settled_at and self.finished_at and self.settled_at < self.finished_at:
            errors['settled_at'] = 'Settlement cannot precede the end of the run.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        using = kwargs.get('using') or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            if self._state.adding and self.assigned_rider_id:
                self.rider_reference = self.assigned_rider_id
            if not self._state.adding:
                previous = type(self).objects.using(using).select_for_update().get(pk=self.pk)
                if previous.started_at and previous.assigned_rider_id != self.assigned_rider_id:
                    raise ValidationError({'assigned_rider': 'A started session keeps its original rider.'})
                if previous.rider_reference != self.rider_reference:
                    raise ValidationError({'rider_reference': 'The rider reference is set by the assigned rider.'})
                if previous.finance_status == self.FinanceStatus.CONFIRMED:
                    financial_fields = ['rider_cut', 'service_charge', 'fuel_surge_addition', 'finance_status', 'settled_at',
                                        'points_awarded', 'session_distance', 'started_at', 'finished_at', 'status', 'date']
                    if any(getattr(previous, name) != getattr(self, name) for name in financial_fields):
                        raise ValidationError('Confirmed session facts cannot be changed; record an adjustment separately.')
                if previous.assigned_rider_id != self.assigned_rider_id and self.assigned_rider_id:
                    self.rider_reference = self.assigned_rider_id
                    if kwargs.get('update_fields') is not None:
                        kwargs['update_fields'] = set(kwargs['update_fields']) | {'rider_reference'}
            self.full_clean()
            result = super().save(*args, **kwargs)
            self.refresh_from_db(fields=['rider_revenue', 'total_revenue', 'session_time'], using=kwargs.get('using'))
            return result

    def __str__(self):
        return f'Session {self.pk} - rider {self.rider_reference} - {self.date}'


class SessionOrder(models.Model):
    class Status(models.TextChoices):
        QUEUED = 'queued', 'Queued'
        DELIVERED = 'delivered', 'Delivered'
        FAILED = 'failed', 'Failed delivery'
        REMOVED = 'removed', 'Released from this run'

    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='session_orders')
    order = models.ForeignKey('orders.Order', on_delete=models.SET_NULL, null=True, blank=True, related_name='session_attempts')
    order_reference = models.PositiveBigIntegerField(editable=False, help_text='Original order ID retained if the source order is deleted.')
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, editable=False, validators=[MinValueValidator(ZERO)])
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    sequence = models.PositiveIntegerField(default=0, help_text='Optional position in the run; zero means not ranked yet.')
    assigned_at = models.DateTimeField(default=timezone.now)
    expected_delivery_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sequence', 'pk']
        indexes = [models.Index(fields=['status', 'finished_at'], name='riders_attempt_finish_idx')]
        constraints = [
            models.UniqueConstraint(fields=['session', 'order_reference'], name='riders_order_once_per_run'),
            models.UniqueConstraint(fields=['order_reference'], condition=models.Q(status__in=['queued', 'delivered']), name='riders_order_one_live_attempt'),
            models.CheckConstraint(condition=models.Q(status__in=['queued', 'delivered', 'failed', 'removed']), name='riders_attempt_valid_status'),
            models.CheckConstraint(condition=models.Q(delivery_fee__gte=0), name='riders_attempt_fee_nonnegative'),
            models.CheckConstraint(condition=(models.Q(status='queued', finished_at__isnull=True) | models.Q(status__in=['delivered', 'failed', 'removed'], finished_at__isnull=False)), name='riders_attempt_has_finish'),
            models.CheckConstraint(condition=(models.Q(finished_at__isnull=True) | models.Q(finished_at__gte=models.F('assigned_at'))), name='riders_attempt_time_order'),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self._state.adding and not self.order_id:
            errors['order'] = 'Choose an order for this delivery attempt.'
        if self.status == self.Status.QUEUED:
            if self.finished_at:
                errors['finished_at'] = 'A queued order has no finish timestamp.'
            if self.session_id and self.session.status in [Session.Status.COMPLETED, Session.Status.CANCELLED]:
                errors['session'] = 'Closed sessions cannot receive queued orders.'
        elif not self.finished_at:
            errors['finished_at'] = 'A resolved delivery attempt needs a finish timestamp.'
        if self.finished_at and self.assigned_at and self.finished_at < self.assigned_at:
            errors['finished_at'] = 'The attempt cannot finish before it was assigned.'
        if self.order_id and self.session_id:
            if self.order.rider_id is not None and self.order.rider_id != self.session.assigned_rider_id:
                errors['order'] = 'The order and session must have the same assigned rider.'
            if self.status == self.Status.QUEUED and self.order.status in ['delivered', 'cancelled']:
                errors['order'] = 'A delivered or cancelled order cannot be queued for another run.'
        if self.status == self.Status.FAILED and not self.failure_reason.strip():
            errors['failure_reason'] = 'Record why the delivery failed.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self._state.adding and self.order_id:
            self.order_reference = self.order_id
            self.delivery_fee = self.order.delivery_fee
        if not self._state.adding:
            previous = type(self).objects.using(kwargs.get('using') or self._state.db or 'default').get(pk=self.pk)
            if (previous.session_id != self.session_id or previous.order_reference != self.order_reference
                    or previous.order_id != self.order_id or previous.delivery_fee != self.delivery_fee):
                raise ValidationError('An attempt keeps its original session, order reference, and delivery fee.')
            if previous.status != self.Status.QUEUED and previous.status != self.status:
                raise ValidationError('Resolved attempts cannot be reopened; create a new attempt for a retry.')
            if previous.status != self.Status.QUEUED:
                facts = ['finished_at', 'assigned_at', 'expected_delivery_at', 'failure_reason']
                if any(getattr(previous, name) != getattr(self, name) for name in facts):
                    raise ValidationError('Resolved delivery facts cannot be overwritten.')
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'Order {self.order_reference} in session {self.session_id}'
