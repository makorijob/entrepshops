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
    while staying one platform underneath. Fully admin-managed — add as
    many departments as your business grows into."""

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
    tagline = models.CharField(max_length=150, blank=True, help_text='Short line shown under the name, e.g. "Wholesale & retail, delivered nationwide".')
    theme = models.CharField(max_length=10, choices=Theme.choices, default=Theme.GREEN)
    unit_kind = models.CharField(
        max_length=10, choices=UnitKind.choices, default=UnitKind.NONE,
        help_text='Controls the "size" label shown on the product page and the unit used on variants. Drinks → volume, Electronics → screen, groceries → weight.'
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
        help_text='Which department this category belongs to (Drinks, Electronics, etc).'
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
        """Everything a customer should be able to see and buy:
        active + approved, AND (platform's own OR a vendor who's
        admin-approved AND either on trial or subscribed).

        Trial vendors' products are visible for the full 30-day trial,
        and stay visible after the trial ends (grace period) — the vendor
        simply can't add new ones."""
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
                # Grace period: once trial expired, existing products
                # still show. So we also allow vendors whose trial has
                # already ended as long as the product is approved.
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
        help_text='Which top-level department this product belongs to. Auto-filled from the category if left blank.'
    )
    category = models.ForeignKey(
        Category, related_name='products', on_delete=models.CASCADE
    )
    vendor = models.ForeignKey(
        'vendors.Vendor', related_name='products', on_delete=models.CASCADE,
        null=True, blank=True,
        help_text='Leave blank for products the platform itself sells. Set this for a third-party seller\'s listing.'
    )
    is_approved = models.BooleanField(
        default=True,
        help_text='Platform-owned products are auto-approved. Vendor listings default to unapproved until admin reviews them — see the "Approve selected products" admin action.'
    )
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=160, unique=True)
    description = models.TextField(blank=True)
    image = models.ImageField(
        upload_to='products/', blank=True, null=True,
        help_text='Main product photo. Additional gallery photos go in the "Images" section below.'
    )
    availability = models.CharField(
        max_length=20, choices=Availability.choices, default=Availability.AVAILABLE,
        help_text='Controls the badge and whether "Add to cart" is enabled.'
    )

    # Fallback pricing/stock — only used if this product has no variants.
    price = models.DecimalField(
        max_digits=8, decimal_places=2, blank=True, null=True,
        help_text='Fallback price in KES. Prefer setting prices on variants below.'
    )
    compare_at_price = models.DecimalField(
        max_digits=8, decimal_places=2, blank=True, null=True,
        help_text='Optional "was" price. Shows a discount badge when higher than price.'
    )
    stock = models.PositiveIntegerField(default=0, help_text='Fallback stock if no variants.')
    volume_ml = models.PositiveIntegerField(
        blank=True, null=True,
        help_text='Legacy single-size field. Use variants for multi-size products.'
    )

    wholesale_quantity_threshold = models.PositiveIntegerField(
        blank=True, null=True,
        help_text='Buying this many or more automatically switches to the wholesale unit price below.'
    )
    wholesale_price = models.DecimalField(
        max_digits=8, decimal_places=2, blank=True, null=True,
        help_text='Per-unit price applied automatically once the quantity threshold is met.'
    )
    is_alcoholic = models.BooleanField(
        default=False,
        help_text='Alcoholic drinks may be restricted from online sale/delivery.',
    )
    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(
        default=False,
        help_text='Show this product in the homepage hero slider.',
    )
    hero_tagline = models.CharField(
        max_length=60, blank=True,
        help_text='Short badge text for the hero slide, e.g. "20% off today". Defaults to "Featured".'
    )
    hero_headline = models.CharField(
        max_length=100, blank=True,
        help_text='Big hero headline. Defaults to the product name if left blank.'
    )
    hero_description = models.CharField(
        max_length=200, blank=True,
        help_text='Short hero copy. Defaults to the product description if left blank.'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['department', 'is_active', 'is_approved']),
            models.Index(fields=['category', 'is_active', 'is_approved']),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.category_id and self.category.department_id:
            if self.department_id and self.department_id != self.category.department_id:
                raise ValidationError({
                    'department': (
                        f'Category "{self.category.name}" belongs to '
                        f'"{self.category.department.name}", not to the department you selected.'
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
        """True if anything is purchasable — a variant with stock, or
        fallback stock for legacy products."""
        if self.has_variants:
            return self.variants.filter(is_active=True, stock__gt=0).exists()
        return self.stock > 0

    @property
    def is_deliverable(self):
        """Respects the DISALLOW_ALCOHOL_DELIVERY setting."""
        from django.conf import settings as dj_settings
        if self.is_alcoholic and dj_settings.DISALLOW_ALCOHOL_DELIVERY:
            return False
        return True

    @property
    def is_available(self):
        """False when discontinued/unavailable, or when nothing is in stock."""
        if self.availability in (self.Availability.DISCONTINUED, self.Availability.UNAVAILABLE):
            return False
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
        """Lowest–highest active variant price, or (price, price) fallback."""
        prices = list(
            self.variants.filter(is_active=True).values_list('price', flat=True)
        )
        if prices:
            return min(prices), max(prices)
        return (self.price or 0), (self.price or 0)

    # ---- images ----
    @property
    def primary_image(self):
        """First ProductImage by sort_order, or fall back to the legacy field."""
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
    """One purchasable size/version of a product — a TV in 32"/40"/43",
    a drink in 500ml/1L/2L, a bag of rice in 1kg/5kg/25kg. Each variant
    has its own price, stock and SKU. The cart stores the variant, not
    the product, so different sizes are priced independently."""

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
    size_label = models.CharField(
        max_length=40,
        help_text='Shown on the size selector button, e.g. 32", 500ml, 1kg.'
    )
    size_value = models.DecimalField(
        max_digits=8, decimal_places=2, blank=True, null=True,
        help_text='Optional numeric value for sorting/filtering, e.g. 500 for 500ml.'
    )
    size_unit = models.CharField(
        max_length=10, choices=SizeUnit.choices, blank=True, default='',
        help_text='Unit that size_value is expressed in.'
    )
    sku = models.CharField(
        max_length=64, blank=True,
        help_text='Optional stock-keeping unit for this exact size.'
    )
    price = models.DecimalField(max_digits=8, decimal_places=2, help_text='Price in KES')
    compare_at_price = models.DecimalField(
        max_digits=8, decimal_places=2, blank=True, null=True,
        help_text='Optional "was" price for this size.'
    )
    stock = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(
        default=True,
        help_text='Turn off to hide this size without deleting it.'
    )
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
    """Extra photos shown in the product gallery — the main hero image
    plus thumbnails. The first image by sort_order is treated as primary."""

    product = models.ForeignKey(
        Product, related_name='images', on_delete=models.CASCADE
    )
    image = models.ImageField(upload_to='products/gallery/')
    alt_text = models.CharField(
        max_length=150, blank=True,
        help_text='Short description of what the photo shows, for screen readers.'
    )
    sort_order = models.PositiveIntegerField(
        default=0, help_text='Lower numbers show first. 0 = main photo.'
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f'{self.product.name} — image #{self.pk}'


class ProductOffer(models.Model):
    """A per-product special offer line — e.g. "Save 20% off TV Wall Mounts
    when you buy a TV". Rendered in the highlighted offers box on the
    product page."""

    product = models.ForeignKey(
        Product, related_name='offers', on_delete=models.CASCADE
    )
    text = models.CharField(max_length=200, help_text='e.g. "Save 20% off TV Wall Mounts when you buy a TV"')
    url = models.CharField(
        max_length=300, blank=True,
        help_text='Optional link. Leave blank for plain text.'
    )
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self):
        return self.text


class BundleOffer(models.Model):
    """A genuine "buy X get Y" deal — auto-applied in the cart, not just
    marketing copy. Buying `trigger_quantity` or more of `trigger_product`
    automatically adds `reward_quantity` of `reward_product` free."""

    label = models.CharField(max_length=150, help_text='Shown on the product, e.g. "Buy 2 get 1 free Sprite 500ml".')
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
    """Upload as many logo images as you like — pick which one is live the
    same way as background images. Falls back to the default line-art
    glass icon + wordmark if none is active."""

    label = models.CharField(max_length=100, blank=True, help_text='Just a label for you, e.g. "2026 rebrand".')
    image = models.ImageField(upload_to='site/logos/', help_text='Shown in the header. A wide/rectangular logo works best.')
    is_active = models.BooleanField(
        default=False,
        help_text='Only one logo is shown at a time — selecting this one deselects any other.'
    )
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
    """A sitewide ambient background photo. Upload as many as you like from
    /admin/ — they form a gallery you can pick from; exactly one is ever
    "active" (shown on the site) at a time."""

    label = models.CharField(
        max_length=100, blank=True, help_text='Just a label for you, e.g. "Festive season".'
    )
    image = models.ImageField(
        upload_to='site/backgrounds/',
        help_text='Sitewide ambient background, shown dimmed behind the glass UI.'
    )
    is_active = models.BooleanField(
        default=False,
        help_text='Only one background is shown at a time — selecting this one deselects any other.'
    )
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
    """A neighbourhood/estate with its own delivery fee and ETA."""

    name = models.CharField(max_length=150, unique=True, help_text='e.g. Kilimani, Westlands')
    delivery_fee = models.DecimalField(max_digits=8, decimal_places=2, help_text='Fee in KES')
    estimated_minutes = models.PositiveIntegerField(
        default=45, help_text='Typical delivery time for this zone, in minutes'
    )
    is_active = models.BooleanField(
        default=True, help_text='Turn off to stop deliveries to this zone.'
    )

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} — KES {self.delivery_fee}'


class DeliveryAddress(models.Model):
    """A saved apartment / doorstep delivery address for a customer."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name='addresses', on_delete=models.CASCADE
    )
    label = models.CharField(
        max_length=50, default='Home', help_text='e.g. Home, Office'
    )
    full_name = models.CharField(max_length=150)
    phone_number = models.CharField(max_length=20, help_text='Used for M-Pesa STK push, e.g. 2547XXXXXXXX')
    building_name = models.CharField(max_length=150)
    apartment_number = models.CharField(max_length=50, blank=True)
    floor = models.CharField(max_length=20, blank=True)
    street = models.CharField(max_length=200)
    area = models.CharField(max_length=150, help_text='Neighbourhood / estate')
    city = models.CharField(max_length=100, default='Nairobi')
    delivery_notes = models.TextField(
        blank=True, help_text='Gate code, landmark, preferred drop-off instructions, etc.'
    )
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, blank=True, null=True,
        help_text='Captured from the browser/device GPS, if allowed.'
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, blank=True, null=True,
        help_text='Captured from the browser/device GPS, if allowed.'
    )
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
    """A Shopify store you sync products from. Supports multiple stores —
    one for each supplier. Each source keeps its own last-sync timestamp
    and status so the admin can see the health of each connection.

    Uses Shopify's Client Credentials Grant: we store the client_id and
    client_secret from the Dev Dashboard, and exchange them for a
    short-lived access token on each sync (cached for ~23h)."""

    class Provider(models.TextChoices):
        SHOPIFY = 'shopify', 'Shopify'

    class Status(models.TextChoices):
        OK = 'ok', 'OK'
        ERROR = 'error', 'Error'
        NEVER = 'never', 'Never synced'

    name = models.CharField(
        max_length=100, unique=True,
        help_text='A label for you, e.g. "Main Shopify Store".'
    )
    provider = models.CharField(
        max_length=20, choices=Provider.choices, default=Provider.SHOPIFY,
    )
    store_domain = models.CharField(
        max_length=200,
        help_text='e.g. your-store.myshopify.com (no https://, no trailing slash).'
    )
    client_id = models.CharField(
        max_length=120, blank=True,
        help_text='Shopify app Client ID from the Dev Dashboard → App settings.'
    )
    client_secret = models.CharField(
        max_length=200, blank=True,
        help_text='Shopify app Client Secret. Treat like a password.'
    )
    api_version = models.CharField(
        max_length=20, default='2024-10',
        help_text='Shopify Admin API version, e.g. 2024-10.'
    )

    default_department = models.ForeignKey(
        'store.Department', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='supplier_sources',
        help_text='Department to assign when the Shopify product has no match.'
    )

    is_active = models.BooleanField(
        default=True,
        help_text='Disable to stop this source from being synced.'
    )
    auto_approve = models.BooleanField(
        default=True,
        help_text='If off, imported products land in "pending approval" and '
                  'you review them in Products admin before they go live.'
    )

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
