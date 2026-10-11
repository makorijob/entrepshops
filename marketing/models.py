import secrets

from django.conf import settings
from django.db import models
from django.db.models import Q, Sum
from django.urls import reverse
from django.utils import timezone


class MarketingCampaign(models.Model):
    """A piece of marketing content the admin publishes for users to
    download and share. One new campaign per day is the expected cadence."""

    class MediaType(models.TextChoices):
        IMAGE = 'image', 'Image'
        VIDEO = 'video', 'Video'

    title = models.CharField(max_length=150)
    description = models.TextField(blank=True, help_text='Shown to users under the media, optional.')
    product = models.ForeignKey(
        'store.Product', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='marketing_campaigns',
        help_text='The product this campaign promotes. Shared links land here.'
    )
    media_type = models.CharField(max_length=10, choices=MediaType.choices, default=MediaType.IMAGE)
    image = models.ImageField(upload_to='marketing/images/', blank=True, null=True)
    video = models.FileField(upload_to='marketing/videos/', blank=True, null=True)
    caption_suggestion = models.TextField(
        blank=True,
        help_text='Suggested WhatsApp caption. Users can copy this with one tap. Use {{LINK}} as a placeholder for the share URL.',
    )
    payout_per_view = models.DecimalField(
        max_digits=6, decimal_places=2, default=50,
        help_text='KES paid to the sharer for each view credited to their share.',
    )
    is_active = models.BooleanField(
        default=True,
        help_text='Turn off to stop new shares and stop counting views.',
    )
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Leave blank to keep it running until you turn it off.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    @property
    def is_live(self):
        now = timezone.now()
        if not self.is_active or now < self.starts_at:
            return False
        if self.ends_at and now > self.ends_at:
            return False
        return True

    @property
    def landing_url(self):
        if self.product:
            return self.product.get_absolute_url()
        return reverse('store:home')


class MarketingShare(models.Model):
    """A single user's tracked share of a single campaign."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='marketing_shares',
    )
    campaign = models.ForeignKey(
        MarketingCampaign, on_delete=models.CASCADE,
        related_name='shares',
    )
    code = models.CharField(max_length=16, unique=True, db_index=True)
    views = models.PositiveIntegerField(default=0)
    earnings = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    last_view_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [('user', 'campaign')]
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user} → {self.campaign} ({self.views} views)'

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self._generate_code()
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_code():
        while True:
            code = secrets.token_urlsafe(6)
            if not MarketingShare.objects.filter(code=code).exists():
                return code

    @property
    def share_url_path(self):
        return reverse('marketing:redirect', args=[self.code])


class MarketingView(models.Model):
    """One row per unique view of a share link."""

    share = models.ForeignKey(
        MarketingShare, on_delete=models.CASCADE, related_name='view_rows',
    )
    fingerprint = models.CharField(max_length=64, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    user_agent = models.CharField(max_length=300, blank=True)
    referer = models.CharField(max_length=300, blank=True)

    class Meta:
        unique_together = [('share', 'fingerprint')]
        ordering = ['-created_at']
        indexes = [models.Index(fields=['share', 'fingerprint'])]


class MarketingProof(models.Model):
    """A user-uploaded screenshot of their WhatsApp Status view count."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending (24h hold)'
        APPROVED = 'approved', 'Approved & credited'
        REJECTED = 'rejected', 'Rejected'

    share = models.ForeignKey(
        MarketingShare, on_delete=models.CASCADE, related_name='proofs',
    )
    screenshot = models.ImageField(upload_to='marketing/proofs/%Y/%m/')
    reported_views = models.PositiveIntegerField(
        help_text='Type the number you see in your screenshot.'
    )
    note = models.TextField(blank=True)

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING,
    )
    payout_due_at = models.DateTimeField(
        db_index=True,
        help_text='When the 24-hour hold expires and the payout can run.',
    )
    credited_at = models.DateTimeField(null=True, blank=True)
    credited_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
    )

    confirmation_sent_at = models.DateTimeField(null=True, blank=True)
    congrats_sent_at = models.DateTimeField(null=True, blank=True)

    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']
        indexes = [
            models.Index(fields=['status', 'payout_due_at']),
        ]

    def __str__(self):
        return f'{self.share.user} — {self.reported_views} views ({self.status})'

    @property
    def is_due(self):
        return (
            self.status == self.Status.PENDING
            and self.payout_due_at <= timezone.now()
        )


class MarketingPayout(models.Model):
    """Earnings summary per user, paid out manually. One row per (user, campaign)."""

    class Status(models.TextChoices):
        ACCRUING = 'accruing', 'Accruing'
        REQUESTED = 'requested', 'Payout requested'
        PAID = 'paid', 'Paid'
        REJECTED = 'rejected', 'Rejected'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='marketing_payouts',
    )
    campaign = models.ForeignKey(
        MarketingCampaign, on_delete=models.CASCADE,
        related_name='payouts',
    )
    views = models.PositiveIntegerField(default=0)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACCRUING)
    requested_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = [('user', 'campaign')]
        ordering = ['-requested_at', '-id']

    def __str__(self):
        return f'{self.user} — {self.campaign} — KES {self.amount}'


class WithdrawalRequest(models.Model):
    """A user's request to withdraw their earnings to an M-Pesa number.

    Used by BOTH marketers (via MarketingShare) and vendors (via Vendor).
    The `vendor` field is set when the request comes from a vendor.
    Processed manually by an admin."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending review'
        APPROVED = 'approved', 'Approved (ready to pay)'
        PAID = 'paid', 'Paid'
        REJECTED = 'rejected', 'Rejected'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='marketing_withdrawals',
    )
    vendor = models.ForeignKey(
        'vendors.Vendor', null=True, blank=True,
        on_delete=models.CASCADE, related_name='withdrawal_requests',
        help_text='Set when the request comes from a vendor (seller) rather than a marketer.',
    )
    phone_number = models.CharField(
        max_length=20,
        help_text='Safaricom number in the format 2547XXXXXXXX.',
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING,
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='marketing_withdrawals_reviewed',
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    mpesa_receipt = models.CharField(
        max_length=40, blank=True,
        help_text='M-Pesa confirmation code after payment.',
    )
    admin_note = models.TextField(blank=True)

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f'{self.user} — KES {self.amount} ({self.status})'


def withdrawable_balance(user):
    """How much this user can still withdraw from their MARKETING earnings."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    if isinstance(user, User):
        user_id = user.pk
    else:
        user_id = user

    credited = (
        MarketingShare.objects.filter(user_id=user_id)
        .aggregate(s=Sum('earnings'))['s'] or 0
    )
    locked = (
        WithdrawalRequest.objects
        .filter(
            user_id=user_id,
            vendor__isnull=True,
            status__in=[
                WithdrawalRequest.Status.PENDING,
                WithdrawalRequest.Status.APPROVED,
                WithdrawalRequest.Status.PAID,
            ],
        )
        .aggregate(s=Sum('amount'))['s'] or 0
    )
    return credited - locked


class MarketerSubscription(models.Model):
    """A user's activation fee for the WhatsApp Share & Earn programme.
    Paid monthly; gates generating new share links and uploading new
    proofs. Existing shares, views, and withdrawals are never affected."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending payment'
        PAID = 'paid', 'Paid'
        FAILED = 'failed', 'Failed'
        REFUNDED = 'refunded', 'Refunded'

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        related_name='marketer_subscription',
        on_delete=models.CASCADE,
    )

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING,
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    period_days = models.PositiveIntegerField(default=30)

    paid_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    # M-Pesa audit fields
    mpesa_phone = models.CharField(max_length=20, blank=True)
    mpesa_checkout_request_id = models.CharField(max_length=64, blank=True)
    mpesa_receipt = models.CharField(max_length=40, blank=True)
    mpesa_result_code = models.CharField(max_length=10, blank=True)
    mpesa_result_desc = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f'{self.user} — KES {self.amount} ({self.status})'

    @property
    def is_active(self):
        if self.status != self.Status.PAID:
            return False
        if not self.expires_at:
            return False
        return timezone.now() < self.expires_at

    @property
    def days_left(self):
        if not self.is_active:
            return 0
        delta = self.expires_at - timezone.now()
        return max(0, delta.days)

    def mark_paid(self, receipt='', result_code='0', result_desc=''):
        """Extend the subscription — adds to any remaining time if active."""
        now = timezone.now()
        base = self.expires_at if (self.expires_at and self.expires_at > now) else now

        self.status = self.Status.PAID
        self.paid_at = now
        self.expires_at = base + timedelta(days=self.period_days)
        self.mpesa_receipt = receipt
        self.mpesa_result_code = result_code
        self.mpesa_result_desc = result_desc[:255]
        self.save(update_fields=[
            'status', 'paid_at', 'expires_at',
            'mpesa_receipt', 'mpesa_result_code', 'mpesa_result_desc',
        ])

    def mark_failed(self, code='', desc=''):
        self.status = self.Status.FAILED
        self.mpesa_result_code = code
        self.mpesa_result_desc = desc[:255]
        self.save(update_fields=['status', 'mpesa_result_code', 'mpesa_result_desc'])


def has_active_marketing_subscription(user):
    """True if the user has an active paid subscription. Admins bypass
    this check in views via user.is_staff."""
    if not user.is_authenticated:
        return False
    sub = getattr(user, 'marketer_subscription', None)
    return bool(sub and sub.is_active)