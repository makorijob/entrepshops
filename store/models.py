from django.conf import settings
from django.db import models
from django.urls import reverse

from .cloudinary_video_storage import VideoCloudinaryStorage


class Promotion(models.Model):
    """An offer/voucher card shown in the homepage carousel. Fully
    admin-managed — add, reorder, or retire offers without touching code.
    Each card can show either a background image or a background video."""

    class Tone(models.TextChoices):
        GREEN = 'green', 'Green'
        ORANGE = 'orange', 'Orange'
        GOLD = 'gold', 'Gold'

    class MediaType(models.TextChoices):
        IMAGE = 'image', 'Image'
        VIDEO = 'video', 'Video'

    kicker = models.CharField(max_length=60, help_text='Small label above the title, e.g. "First order", "This week", "Gifting".')
    title = models.CharField(max_length=150, help_text='e.g. "20% off your first cart".')
    description = models.TextField(blank=True)
    voucher_code = models.CharField(max_length=30, blank=True, help_text='e.g. WELCOME20. Leave blank to hide the code chip.')
    tone = models.CharField(max_length=10, choices=Tone.choices, default=Tone.GREEN)

    media_type = models.CharField(max_length=10, choices=MediaType.choices, default=MediaType.IMAGE)
    image = models.ImageField(
        upload_to='promotions/images/', blank=True, null=True,
        help_text='Used when Media type is "Image". Stored on Cloudinary.'
    )
    video = models.FileField(
        upload_to='promotions/videos/', blank=True, null=True,
        storage=VideoCloudinaryStorage(),
        help_text='Used when Media type is "Video". MP4 recommended, keep it short — it autoplays muted and loops.'
    )
    video_url = models.URLField(
        blank=True,
        help_text='Alternative to uploading a file — a direct link to an already-hosted MP4. Used if no video file is uploaded.'
    )

    cta_label = models.CharField(max_length=40, default='Shop now')
    cta_url = models.CharField(max_length=200, blank=True, help_text='Where the button links. Leave blank to link to the shop.')

    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0, help_text='Lower numbers show first.')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sort_order', '-created_at']

    def __str__(self):
        return self.title

    @property
    def video_source(self):
        """Whichever video source is set — an uploaded file wins over a URL."""
        if self.media_type != self.MediaType.VIDEO:
            return None
        if self.video:
            return self.video.url
        return self.video_url or None


class Department(models.Model):
    """A top-level shopping vertical — Drinks, Electronics, Vehicles,
    Fashion, etc. Each one carries its own visual theme, so the whole
    site re-skins itself depending on what the customer is browsing,
    while staying one platform underneath."""

    class Theme(models.TextChoices):
        GREEN = 'green', 'Green & gold (drinks, food, everyday goods)'
        BLUE = 'blue', 'Blue & cyan (electronics, tech, gadgets)'
        CRIMSON = 'crimson', 'Crimson & steel (vehicles, motorbikes, hardware)'
        VIOLET = 'violet', 'Violet & rose (fashion, beauty, lifestyle)'
        AMBER = 'amber', 'Amber & bronze (general / anything else)'

    class UnitKind(models.TextChoices):
        VOLUME = 'volume', 'Volume (ml / L)'
        WEIGHT = 'weight', 'Weight (g / kg)'
        SCREEN = 'screen', 'Screen size (inches)'
        NONE = 'none', 'No size variants'

    name = models.CharField(max_length=100, unique=True, help_text='e.g. Drinks, Electronics, Vehicles, Fashion.')
    slug = models.SlugField(max_length=110, unique=True, blank=True)
    tagline = models.CharField(max_length=150, blank=True, help_text='Short line shown under the name.')
    theme = models.CharField(max_length=10, choices=Theme.choices, default=Theme.GREEN)
    unit_kind = models.CharField(
        max_length=10, choices=UnitKind.choices, default=UnitKind.NONE,
        help_text='Controls the "size" label shown on the product page.'
    )
    icon_kind = models.CharField(
        max_length=20, default='bottle',
        choices=[
            ('bottle', 'Bottle'), ('glass', 'Glass'), ('can', 'Can'),
            ('truck', 'Delivery truck'), ('gift', 'Gift'), ('star', 'Star'),
        ],
        help_text='Placeholder icon used where a product in this department has no photo.'
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Category(models.Model):
    department = models.ForeignKey(
        Department, related_name='categories', on_delete=models.CASCADE,
        null=True, blank=True,
        help_text='Which department this category belongs to.'
    )
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=110, unique=True)

    class Meta:
        verbose_name_plural = 'categories'
        ordering = ['name']

    def __str__(self):
        return self.name


class ProductQuerySet(models.QuerySet):
    def visible(self):
        from django.utils import timezone
        now = timezone.now()
        return self.filter(is_active=True, is_approved=True).exclude(
            availability=Product.Availability.DISCONTINUED
        ).filter(
            models.Q(vendor__isnull=True) |
            models.Q(
                vendor__is_approved=True,
            ) & (
                models.Q(vendor__subscription_expires_at__gt=now) |
                models.Q(vendor__trial_ends_at__gt=now) |
                models.Q(vendor__trial_ends_at__isnull=False) |
                models.Q(vendor__subscription_expires_at__isnull=False)
            )
        ).distinct()

    def in_stock(self):
        return self.filter(
            models.Q(variants__is_active=True, variants__stock__gt=0) |
            models.Q(variants__isnull=True, stock__gt=0)
        ).distinct()


class Product(models.Model):
    class Availability(models.TextChoices):
        AVAILABLE = 'available', 'Available'
        LAST_CHANCE = 'last_chance', 'Last chance'
        UNAVAILABLE = 'unavailable', 'Temporarily unavailable'
        DISCONTINUED = 'discontinued', 'No longer available'

    department = models.ForeignKey(
        Department, related_name='products', on_delete=models.PROTECT,
        null=True, blank=True,
    )
    category = models.ForeignKey(
        Category, related_name='products', on_delete=models.CASCADE
    )
    vendor = models.ForeignKey(
        'vendors.Vendor', related_name='products', on_delete=models.CASCADE,
        null=True, blank=True,
    )
    is_approved = models.BooleanField(default=True)
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=160, unique=True)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to='products/', blank=True, null=True)
    availability = models.CharField(
        max_length=20, choices=Availability.choices, default=Availability.AVAILABLE,
    )

    # Fallback pricing/stock — only used if this product has no variants.
    price = models.DecimalField(max_digits=8, decimal_places=2, blank=True, null=True)
    compare_at_price = models.DecimalField(max_digits=8, decimal_places=2, blank=True, null=True)
    stock = models.PositiveIntegerField(default=0)
    volume_ml = models.PositiveIntegerField(blank=True, null=True)

    wholesale_quantity_threshold = models.PositiveIntegerField(blank=True, null=True)
    wholesale_price = models.DecimalField(max_digits=8, decimal_places=2, blank=True, null=True)
    is_alcoholic = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(
        default=False,
        help_text='Show this product in the homepage hero slider.',
    )
    hero_tagline = models.CharField(max_length=60, blank=True)
    hero_headline = models.CharField(max_length=100, blank=True)
    hero_description = models.CharField(max_length=200, blank=True)

    # ---- Rental / hire ----
    is_rentable = models.BooleanField(
        default=False,
        help_text='Tick to make this a rental item. The product page will show a '
                  'booking form instead of "Add to cart".',
    )
    daily_rate = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True,
        help_text='Price per day, in KES. Only used when "is rentable" is ticked.',
    )
    rental_deposit = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True,
        help_text='Refundable deposit collected at booking, in KES. Vendor '
                  'refunds after the car is returned.',
    )
    minimum_days = models.PositiveIntegerField(
        default=1,
        help_text='Minimum rental period in days. Set by the vendor per car.',
    )
    rental_pickup_notes = models.TextField(
        blank=True,
        help_text='Optional notes shown to the customer before booking.',
    )

    created_at = models.DateTimeField(auto_now_add=True)

    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['department', 'is_active', 'is_approved']),
            models.Index(fields=['category', 'is_active', 'is_approved']),
            models.Index(fields=['is_rentable', 'is_active', 'is_approved']),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.category_id and self.category.department_id:
            if self.department_id and self.department_id != self.category.department_id:
                raise ValidationError({
                    'department': (
                        f'Category "{self.category.name}" belongs to '
                        f'"{self.category.department.name}", not to the '
                        f'department you selected.'
                    )
                })

    def save(self, *args, **kwargs):
        if not self.department_id and self.category_id:
            self.department_id = self.category.department_id
        if self.vendor_id and not self.pk:
            self.is_approved = False
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('store:product_detail', args=[self.slug])

    # ---- availability & stock ----
    @property
    def in_stock(self):
        if self.has_variants:
            return self.variants.filter(is_active=True, stock__gt=0).exists()
        return self.stock > 0

    @property
    def is_deliverable(self):
        from django.conf import settings as dj_settings
        if self.is_alcoholic and dj_settings.DISALLOW_ALCOHOL_DELIVERY:
            return False
        return True

    @property
    def is_available(self):
        if self.availability in (self.Availability.DISCONTINUED, self.Availability.UNAVAILABLE):
            return False
        if self.is_rentable:
            # Rentables are always "available" — their availability is
            # determined by the booking calendar, not by stock.
            return True
        return self.in_stock

    # ---- variants ----
    @property
    def has_variants(self):
        return self.variants.exists()

    @property
    def available_variants(self):
        return self.variants.filter(is_active=True).order_by('sort_order', 'price')

    @property
    def price_range(self):
        prices = list(
            self.variants.filter(is_active=True).values_list('price', flat=True)
        )
        if prices:
            return min(prices), max(prices)
        return (self.price or 0), (self.price or 0)

    # ---- images ----
    @property
    def primary_image(self):
        img = self.images.order_by('sort_order', 'id').first()
        return img.image if img else self.image

    # ---- ratings ----
    @property
    def average_rating(self):
        agg = self.reviews.aggregate(avg=models.Avg('rating'), count=models.Count('id'))
        return agg['avg'], agg['count']

    @property
    def rating_rounded(self):
        avg, count = self.average_rating
        if not count:
            return 0
        return round(avg)

    # ---- pricing ----
    @property
    def discount_percent(self):
        if self.compare_at_price and self.price and self.compare_at_price > self.price:
            return round((1 - (self.price / self.compare_at_price)) * 100)
        return None

    @property
    def has_wholesale_price(self):
        return bool(self.wholesale_quantity_threshold and self.wholesale_price is not None)

    def unit_price_for_quantity(self, quantity):
        if self.has_wholesale_price and quantity >= self.wholesale_quantity_threshold:
            return self.wholesale_price
        return self.price


class ProductVariant(models.Model):
    class SizeUnit(models.TextChoices):
        NONE = '', '— (no unit)'
        INCH = 'in', 'Inches'
        ML = 'ml', 'Millilitres'
        L = 'l', 'Litres'
        G = 'g', 'Grams'
        KG = 'kg', 'Kilograms'
        CM = 'cm', 'Centimetres'
        PACK = 'pack', 'Pack'

    product = models.ForeignKey(
        Product, related_name='variants', on_delete=models.CASCADE
    )
    size_label = models.CharField(max_length=40)
    size_value = models.DecimalField(max_digits=8, decimal_places=2, blank=True, null=True)
    size_unit = models.CharField(
        max_length=10, choices=SizeUnit.choices, blank=True, default='',
    )
    sku = models.CharField(max_length=64, blank=True)
    price = models.DecimalField(max_digits=8, decimal_places=2, help_text='Price in KES')
    compare_at_price = models.DecimalField(max_digits=8, decimal_places=2, blank=True, null=True)
    stock = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'price']
        unique_together = [('product', 'size_label')]

    def __str__(self):
        return f'{self.product.name} — {self.size_label}'

    @property
    def discount_percent(self):
        if self.compare_at_price and self.compare_at_price > self.price:
            return round((1 - (self.price / self.compare_at_price)) * 100)
        return None


class ProductImage(models.Model):
    product = models.ForeignKey(
        Product, related_name='images', on_delete=models.CASCADE
    )
    image = models.ImageField(upload_to='products/gallery/')
    alt_text = models.CharField(max_length=150, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f'{self.product.name} — image #{self.pk}'


class ProductOffer(models.Model):
    product = models.ForeignKey(
        Product, related_name='offers', on_delete=models.CASCADE
    )
    text = models.CharField(max_length=200)
    url = models.CharField(max_length=300, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self):
        return self.text


class BundleOffer(models.Model):
    label = models.CharField(max_length=150)
    trigger_product = models.ForeignKey(Product, related_name='bundle_triggers', on_delete=models.CASCADE)
    trigger_quantity = models.PositiveIntegerField(default=2)
    reward_product = models.ForeignKey(Product, related_name='bundle_rewards', on_delete=models.CASCADE)
    reward_quantity = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-id']

    def __str__(self):
        return self.label


class SiteLogo(models.Model):
    label = models.CharField(max_length=100, blank=True)
    image = models.ImageField(upload_to='site/logos/')
    is_active = models.BooleanField(default=False)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']
        verbose_name = 'site logo'
        verbose_name_plural = 'site logos'

    def __str__(self):
        return self.label or f'Logo #{self.pk}'

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        if is_new and not SiteLogo.objects.filter(is_active=True).exists():
            self.is_active = True
        super().save(*args, **kwargs)
        if self.is_active:
            SiteLogo.objects.exclude(pk=self.pk).filter(is_active=True).update(is_active=False)

    @classmethod
    def get_active(cls):
        return cls.objects.filter(is_active=True).first()


class BackgroundImage(models.Model):
    label = models.CharField(max_length=100, blank=True)
    image = models.ImageField(upload_to='site/backgrounds/')
    is_active = models.BooleanField(default=False)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']
        verbose_name = 'background image'
        verbose_name_plural = 'background images'

    def __str__(self):
        return self.label or f'Background #{self.pk}'

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        if is_new and not BackgroundImage.objects.filter(is_active=True).exists():
            self.is_active = True
        super().save(*args, **kwargs)
        if self.is_active:
            BackgroundImage.objects.exclude(pk=self.pk).filter(is_active=True).update(is_active=False)

    @classmethod
    def get_active(cls):
        return cls.objects.filter(is_active=True).first()


class DeliveryZone(models.Model):
    name = models.CharField(max_length=150, unique=True)
    delivery_fee = models.DecimalField(max_digits=8, decimal_places=2)
    estimated_minutes = models.PositiveIntegerField(default=45)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} — KES {self.delivery_fee}'


class DeliveryAddress(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name='addresses', on_delete=models.CASCADE
    )
    label = models.CharField(max_length=50, default='Home')
    full_name = models.CharField(max_length=150)
    phone_number = models.CharField(max_length=20)
    building_name = models.CharField(max_length=150)
    apartment_number = models.CharField(max_length=50, blank=True)
    floor = models.CharField(max_length=20, blank=True)
    street = models.CharField(max_length=200)
    area = models.CharField(max_length=150)
    city = models.CharField(max_length=100, default='Nairobi')
    delivery_notes = models.TextField(blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = 'delivery addresses'

    def __str__(self):
        return f'{self.label} — {self.building_name}, {self.area}'

    @property
    def has_gps(self):
        return self.latitude is not None and self.longitude is not None

    @property
    def maps_url(self):
        if self.has_gps:
            return f'https://www.google.com/maps?q={self.latitude},{self.longitude}'
        return None

    def as_text(self):
        parts = [
            self.building_name,
            f'Apt {self.apartment_number}' if self.apartment_number else '',
            f'Floor {self.floor}' if self.floor else '',
            self.street,
            self.area,
            self.city,
        ]
        return ', '.join(p for p in parts if p)


class SupplierSource(models.Model):
    class Provider(models.TextChoices):
        SHOPIFY = 'shopify', 'Shopify'

    class Status(models.TextChoices):
        OK = 'ok', 'OK'
        ERROR = 'error', 'Error'
        NEVER = 'never', 'Never synced'

    name = models.CharField(max_length=100, unique=True)
    provider = models.CharField(
        max_length=20, choices=Provider.choices, default=Provider.SHOPIFY,
    )
    store_domain = models.CharField(max_length=200)
    client_id = models.CharField(max_length=120, blank=True)
    client_secret = models.CharField(max_length=200, blank=True)
    api_version = models.CharField(max_length=20, default='2024-10')

    default_department = models.ForeignKey(
        'store.Department', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='supplier_sources',
    )

    is_active = models.BooleanField(default=True)
    auto_approve = models.BooleanField(default=True)

    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.NEVER,
    )
    last_error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.store_domain})'


class VehicleRental(models.Model):
    """A booking for a rentable vehicle. Fully standalone — doesn't go
    through the cart. One M-Pesa payment covers the full rental period
    plus a refundable deposit. The vendor refunds the deposit outside
    the system after the vehicle is returned in good condition.
    """

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending payment'
        CONFIRMED = 'confirmed', 'Confirmed — ready for pickup'
        ACTIVE = 'active', 'Currently rented out'
        RETURNED = 'returned', 'Returned'
        CANCELLED = 'cancelled', 'Cancelled'
        FAILED = 'failed', 'Payment failed'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='vehicle_rentals',
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name='rentals',
    )
    vendor = models.ForeignKey(
        'vendors.Vendor', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='rentals',
    )

    pickup_date = models.DateField()
    return_date = models.DateField()
    total_days = models.PositiveIntegerField()

    daily_rate = models.DecimalField(max_digits=10, decimal_places=2)
    rental_deposit = models.DecimalField(max_digits=10, decimal_places=2)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)

    delivery_full_name = models.CharField(max_length=150)
    delivery_phone = models.CharField(max_length=20)
    delivery_address = models.TextField()
    delivery_notes = models.TextField(blank=True)

    driver_license = models.ImageField(upload_to='rentals/licenses/%Y/%m/')
    customer_notes = models.TextField(blank=True)

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING,
    )
    mpesa_phone = models.CharField(max_length=20, blank=True)
    mpesa_checkout_request_id = models.CharField(max_length=64, blank=True)
    mpesa_receipt = models.CharField(max_length=40, blank=True)
    mpesa_result_code = models.CharField(max_length=10, blank=True)
    mpesa_result_desc = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['product', 'pickup_date', 'return_date']),
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['status', 'pickup_date']),
        ]
        verbose_name = 'vehicle rental'
        verbose_name_plural = 'vehicle rentals'

    def __str__(self):
        return f'{self.product.name} — {self.pickup_date} to {self.return_date} ({self.status})'

    @classmethod
    def has_overlap(cls, product, pickup_date, return_date, exclude_pk=None):
        """True if the vehicle is already booked for any overlapping window."""
        qs = cls.objects.filter(
            product=product,
            status__in=[cls.Status.CONFIRMED, cls.Status.ACTIVE],
            pickup_date__lt=return_date,
            return_date__gt=pickup_date,
        )
        if exclude_pk:
            qs = qs.exclude(pk=exclude_pk)
        return qs.exists()

    @property
    def is_past(self):
        from django.utils import timezone
        return self.return_date < timezone.localdate()

    def mark_paid(self, receipt='', result_code='0', result_desc=''):
        from django.utils import timezone
        self.status = self.Status.CONFIRMED
        self.paid_at = timezone.now()
        self.mpesa_receipt = receipt
        self.mpesa_result_code = result_code
        self.mpesa_result_desc = result_desc[:255]
        self.save(update_fields=[
            'status', 'paid_at',
            'mpesa_receipt', 'mpesa_result_code', 'mpesa_result_desc',
        ])

    def mark_failed(self, code='', desc=''):
        self.status = self.Status.FAILED
        self.mpesa_result_code = code
        self.mpesa_result_desc = desc[:255]
        self.save(update_fields=['status', 'mpesa_result_code', 'mpesa_result_desc'])

    def mark_returned(self):
        from django.utils import timezone
        self.status = self.Status.RETURNED
        self.returned_at = timezone.now()
        self.save(update_fields=['status', 'returned_at'])

    def mark_cancelled(self):
        from django.utils import timezone
        self.status = self.Status.CANCELLED
        self.cancelled_at = timezone.now()
        self.save(update_fields=['status', 'cancelled_at'])
