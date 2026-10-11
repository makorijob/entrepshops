from django.conf import settings
from django.db import models
from django.utils import timezone

from store.models import DeliveryAddress, Product


class Rider(models.Model):
    """A delivery rider. Optionally linked to a login account (rider_profile)
    so they can use the rider dashboard to update their own deliveries."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, related_name='rider_profile',
        on_delete=models.CASCADE, null=True, blank=True,
        help_text='Optional login account so this rider can use the rider dashboard.'
    )
    name = models.CharField(max_length=150)
    phone_number = models.CharField(max_length=20)
    vehicle = models.CharField(max_length=100, blank=True, help_text='e.g. Motorbike, Bicycle, Van')
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING_PAYMENT = 'pending_payment', 'Pending payment'
        PAID = 'paid', 'Paid'
        PREPARING = 'preparing', 'Preparing'
        OUT_FOR_DELIVERY = 'out_for_delivery', 'Out for delivery'
        DELIVERED = 'delivered', 'Delivered'
        CANCELLED = 'cancelled', 'Cancelled'

    # Ordered timeline of "real progress" statuses used to render the
    # customer-facing tracker. pending_payment/cancelled are handled
    # separately since they aren't points on a forward-moving line.
    TIMELINE = [
        (Status.PAID, 'Order placed'),
        (Status.PREPARING, 'Preparing'),
        (Status.OUT_FOR_DELIVERY, 'Out for delivery'),
        (Status.DELIVERED, 'Delivered'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name='orders', on_delete=models.CASCADE
    )
    delivery_address = models.ForeignKey(
        DeliveryAddress, related_name='orders', on_delete=models.PROTECT
    )
    rider = models.ForeignKey(
        Rider, related_name='orders', null=True, blank=True, on_delete=models.SET_NULL
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING_PAYMENT
    )
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    delivery_fee = models.DecimalField(max_digits=8, decimal_places=2)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    phone_number = models.CharField(max_length=20, help_text='Phone used for M-Pesa payment')
    cancellation_reason = models.CharField(
        max_length=255, blank=True,
        help_text='Shown to the customer — e.g. "You cancelled the M-Pesa prompt" or "Payment failed: insufficient funds".'
    )
    estimated_minutes = models.PositiveIntegerField(
        blank=True, null=True,
        help_text='Estimated delivery time in minutes, captured from the delivery zone at checkout.'
    )
    assigned_at = models.DateTimeField(blank=True, null=True)
    out_for_delivery_at = models.DateTimeField(blank=True, null=True)
    delivered_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Order #{self.id} — {self.user}'

    def mark_paid(self):
        from . import emails
        self.status = self.Status.PAID
        self.save(update_fields=['status', 'updated_at'])
        emails.send_payment_confirmation_email(self)
        emails.send_order_received_email(self)
        self._release_referral_bonus_if_first_order()

    def mark_cancelled(self, reason):
        """Called when payment fails or is cancelled, so the order doesn't
        sit forever as a vague 'pending' entry — the customer gets a clear,
        specific reason instead."""
        self.status = self.Status.CANCELLED
        self.cancellation_reason = reason[:255]
        self.save(update_fields=['status', 'cancellation_reason', 'updated_at'])

    def _release_referral_bonus_if_first_order(self):
        """A referral only pays out once the referred user has completed
        a genuine first paid order — not just at signup. As an extra
        anti-fraud check, we also refuse to pay out if this order's phone
        number has already been used for a paid order by a DIFFERENT
        user — that's the signature of someone farming referral bonuses
        with fake accounts that all funnel back to one real phone."""
        had_earlier_paid_order = Order.objects.filter(
            user=self.user,
            status__in=[self.Status.PAID, self.Status.PREPARING, self.Status.OUT_FOR_DELIVERY, self.Status.DELIVERED],
        ).exclude(pk=self.pk).exists()
        if had_earlier_paid_order:
            return

        phone_reused_by_someone_else = Order.objects.filter(
            phone_number=self.phone_number,
            status__in=[self.Status.PAID, self.Status.PREPARING, self.Status.OUT_FOR_DELIVERY, self.Status.DELIVERED],
        ).exclude(user=self.user).exists()
        if phone_reused_by_someone_else:
            return

        profile = getattr(self.user, 'profile', None)
        if profile:
            profile.release_first_order_bonus_if_due()

    def assign_rider(self, rider):
        """Assign a rider and, if the order is only just paid, bump it into
        'preparing' — assignment implies someone has started on the order."""
        self.rider = rider
        self.assigned_at = timezone.now()
        if self.status == self.Status.PAID:
            self.status = self.Status.PREPARING
        self.save(update_fields=['rider', 'assigned_at', 'status', 'updated_at'])

    def mark_out_for_delivery(self):
        from . import emails
        self.status = self.Status.OUT_FOR_DELIVERY
        self.out_for_delivery_at = timezone.now()
        self.save(update_fields=['status', 'out_for_delivery_at', 'updated_at'])
        emails.send_out_for_delivery_email(self)

    def mark_delivered(self):
        from . import emails
        self.status = self.Status.DELIVERED
        self.delivered_at = timezone.now()
        self.save(update_fields=['status', 'delivered_at', 'updated_at'])
        emails.send_delivered_email(self)

    @property
    def is_delivered(self):
        return self.status == self.Status.DELIVERED

    @property
    def status_timeline(self):
        """Ordered steps for the customer-facing progress tracker, each
        flagged done/active/upcoming based on the order's current status."""
        timestamps = {
            self.Status.PAID: self.created_at,
            self.Status.PREPARING: self.assigned_at,
            self.Status.OUT_FOR_DELIVERY: self.out_for_delivery_at,
            self.Status.DELIVERED: self.delivered_at,
        }
        keys = [key for key, _ in self.TIMELINE]
        current_index = keys.index(self.status) if self.status in keys else -1
        steps = []
        for i, (key, label) in enumerate(self.TIMELINE):
            steps.append({
                'key': key,
                'label': label,
                'timestamp': timestamps.get(key),
                'done': current_index >= 0 and i < current_index,
                'active': i == current_index,
                'upcoming': current_index >= 0 and i > current_index,
            })
        return steps


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, related_name='order_items', on_delete=models.PROTECT)
    unit_price = models.DecimalField(max_digits=8, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)

    def __str__(self):
        return f'{self.quantity} x {self.product.name}'

    @property
    def subtotal(self):
        return self.unit_price * self.quantity

    @property
    def review(self):
        return getattr(self, 'order_review', None)


class Review(models.Model):
    """A customer's rating/comment on one item from a delivered order."""

    order_item = models.OneToOneField(OrderItem, related_name='order_review', on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='reviews', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, related_name='reviews', on_delete=models.CASCADE)
    rating = models.PositiveSmallIntegerField(choices=[(i, str(i)) for i in range(1, 6)])
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.rating}★ — {self.product.name} by {self.user}'


class RiderTip(models.Model):
    """A tip a customer sends after delivery, paid via a separate M-Pesa STK push."""

    class Status(models.TextChoices):
        INITIATED = 'initiated', 'Initiated'
        SUCCESS = 'success', 'Success'
        FAILED = 'failed', 'Failed'
        CANCELLED = 'cancelled', 'Cancelled'

    order = models.ForeignKey(Order, related_name='tips', on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    phone_number = models.CharField(max_length=20)
    checkout_request_id = models.CharField(max_length=100, blank=True, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.INITIATED)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'KES {self.amount} tip on order #{self.order_id} — {self.status}'
