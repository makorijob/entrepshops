from django import forms
from django.contrib import admin, messages
from django.db import models
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from .models import (
    BackgroundImage, BundleOffer, Category, DeliveryAddress, DeliveryZone,
    Department, Product, ProductImage, ProductOffer, ProductVariant,
    Promotion, SiteLogo, SupplierSource, VehicleRental,
)


# ======================================================================
# Catalog
# ======================================================================

@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'theme', 'unit_kind', 'is_active', 'sort_order')
    list_filter = ('theme', 'unit_kind', 'is_active')
    search_fields = ('name',)
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'department', 'slug')
    list_filter = ('department',)
    search_fields = ('name',)
    prepopulated_fields = {'slug': ('name',)}


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1
    fields = ('size_label', 'size_value', 'size_unit', 'sku', 'price',
              'compare_at_price', 'stock', 'is_active', 'sort_order')
    ordering = ('sort_order', 'price')


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    fields = ('image', 'alt_text', 'sort_order')


class ProductOfferInline(admin.TabularInline):
    model = ProductOffer
    extra = 1
    fields = ('text', 'url', 'sort_order', 'is_active')


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'department', 'category', 'vendor', 'price_range_display',
        'stock', 'availability', 'is_active', 'is_approved',
        'is_featured', 'is_rentable', 'hero_slide_preview',
    )
    list_filter = (
        'department', 'category', 'vendor', 'availability', 'is_alcoholic',
        'is_active', 'is_approved', 'is_featured', 'is_rentable',
    )
    search_fields = ('name', 'description')
    prepopulated_fields = {'slug': ('name',)}
    actions = ['approve_products', 'feature_in_hero', 'unfeature_from_hero']
    inlines = [ProductVariantInline, ProductImageInline, ProductOfferInline]
    fieldsets = (
        (None, {
            'fields': (
                'department', 'category', 'vendor', 'is_approved',
                'name', 'slug', 'description', 'image',
            )
        }),
        ('Availability', {
            'fields': ('availability', 'is_active'),
        }),
        ('Fallback pricing & stock', {
            'fields': ('price', 'compare_at_price', 'volume_ml', 'stock', 'is_alcoholic'),
        }),
        ('Wholesale / bulk pricing', {
            'fields': ('wholesale_quantity_threshold', 'wholesale_price'),
            'classes': ('collapse',),
        }),
        ('Homepage hero slider', {
            'fields': ('is_featured', 'hero_tagline', 'hero_headline', 'hero_description'),
            'classes': ('collapse',),
        }),
        ('Rental / hire', {
            'fields': (
                'is_rentable', 'daily_rate', 'rental_deposit',
                'minimum_days', 'rental_pickup_notes',
            ),
            'description': (
                'Tick "is rentable" to convert this listing into a hire product. '
                'The product page will show a booking form instead of "Add to cart".'
            ),
            'classes': ('collapse',),
        }),
    )

    @admin.display(description='Price')
    def price_range_display(self, obj):
        if obj.is_rentable and obj.daily_rate:
            return f'KES {obj.daily_rate}/day'
        lo, hi = obj.price_range
        if lo is None or hi is None:
            return '—'
        return f'KES {lo}' if lo == hi else f'KES {lo}–{hi}'

    @admin.display(description='Hero slide')
    def hero_slide_preview(self, obj):
        if not obj.is_featured:
            return '—'
        headline = obj.hero_headline or obj.name
        tagline = obj.hero_tagline or 'Featured'
        return format_html(
            '<span style="display:inline-block;padding:2px 8px;border-radius:10px;'
            'background:#eef7ee;color:#256b2c;font-size:11px;">{} · {}</span>',
            tagline, headline,
        )

    @admin.action(description='Approve selected products')
    def approve_products(self, request, queryset):
        updated = queryset.update(is_approved=True)
        self.message_user(request, f'{updated} product(s) approved.')

    @admin.action(description='Feature selected products in the homepage hero')
    def feature_in_hero(self, request, queryset):
        updated = queryset.update(is_featured=True)
        self.message_user(request, f'{updated} product(s) added to the homepage hero.')

    @admin.action(description='Remove selected products from the homepage hero')
    def unfeature_from_hero(self, request, queryset):
        updated = queryset.update(is_featured=False)
        self.message_user(request, f'{updated} product(s) removed from the homepage hero.')


@admin.register(BundleOffer)
class BundleOfferAdmin(admin.ModelAdmin):
    list_display = ('label', 'trigger_product', 'trigger_quantity',
                    'reward_product', 'reward_quantity', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('label',)


# ======================================================================
# Site chrome
# ======================================================================

class PromotionAdminForm(forms.ModelForm):
    IMAGE_EXTS = ('jpg', 'jpeg', 'png', 'webp', 'gif', 'avif')
    VIDEO_EXTS = ('mp4', 'webm', 'mov', 'm4v')

    class Meta:
        model = Promotion
        fields = '__all__'

    @staticmethod
    def _ext(filename):
        return (filename.rsplit('.', 1)[-1] or '').lower()

    def clean_image(self):
        image = self.cleaned_data.get('image')
        if image and getattr(image, '_committed', True) is False:
            ext = self._ext(getattr(image, 'name', '') or '')
            if ext and ext not in self.IMAGE_EXTS:
                raise forms.ValidationError(
                    f'"{image.name}" is not an image. Use the Video field for MP4/WebM.'
                )
        return image

    def clean_video(self):
        video = self.cleaned_data.get('video')
        if video and getattr(video, '_committed', True) is False:
            ext = self._ext(getattr(video, 'name', '') or '')
            if ext and ext not in self.VIDEO_EXTS:
                raise forms.ValidationError(
                    f'"{video.name}" is not a supported video. Use MP4 or WebM.'
                )
        return video

    def clean(self):
        cleaned = super().clean()
        media_type = cleaned.get('media_type')
        image = cleaned.get('image')
        video = cleaned.get('video')
        video_url = cleaned.get('video_url')

        if media_type == Promotion.MediaType.VIDEO:
            if image:
                self.add_error('image', 'Media type is "Video" — clear the Image field.')
            if not (video or video_url):
                self.add_error('video', 'Media type is "Video" — upload an MP4 or provide a Video URL.')
        elif media_type == Promotion.MediaType.IMAGE:
            if video:
                self.add_error('video', 'Media type is "Image" — clear the Video field.')
            if video_url:
                self.add_error('video_url', 'Media type is "Image" — clear the Video URL.')
            if not image:
                self.add_error('image', 'Media type is "Image" — upload an image.')

        return cleaned


@admin.register(Promotion)
class PromotionAdmin(admin.ModelAdmin):
    form = PromotionAdminForm
    list_display = ('title', 'kicker', 'tone', 'media_type', 'is_active', 'sort_order')
    list_filter = ('tone', 'media_type', 'is_active')
    search_fields = ('title', 'kicker')


@admin.register(SiteLogo)
class SiteLogoAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'is_active', 'uploaded_at')
    list_filter = ('is_active',)


@admin.register(BackgroundImage)
class BackgroundImageAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'is_active', 'uploaded_at')
    list_filter = ('is_active',)


# ======================================================================
# Delivery
# ======================================================================

@admin.register(DeliveryZone)
class DeliveryZoneAdmin(admin.ModelAdmin):
    list_display = ('name', 'delivery_fee', 'estimated_minutes', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name',)


@admin.register(DeliveryAddress)
class DeliveryAddressAdmin(admin.ModelAdmin):
    list_display = ('label', 'user', 'building_name', 'area', 'city', 'is_default')
    list_filter = ('is_default', 'city')
    search_fields = ('user__username', 'building_name', 'area', 'phone_number')
    raw_id_fields = ('user',)


# ======================================================================
# Supplier sources
# ======================================================================

@admin.register(SupplierSource)
class SupplierSourceAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'provider', 'store_domain',
        'is_active', 'auto_approve',
        'last_status', 'last_synced_at',
        'preview_button',
    )
    list_filter = ('provider', 'is_active', 'last_status')
    search_fields = ('name', 'store_domain')
    readonly_fields = ('last_synced_at', 'last_status', 'last_error')
    fieldsets = (
        (None, {'fields': ('name', 'provider', 'is_active')}),
        ('Shopify connection', {
            'fields': ('store_domain', 'api_version', 'client_id', 'client_secret'),
        }),
        ('Mapping', {'fields': ('default_department',)}),
        ('Behaviour', {'fields': ('auto_approve',)}),
        ('Status', {'fields': ('last_synced_at', 'last_status', 'last_error')}),
    )

    def get_urls(self):
        urls = super().get_urls()
        extra = [
            path(
                '<int:pk>/preview/',
                self.admin_site.admin_view(self.preview_view),
                name='store_suppliersource_preview',
            ),
            path(
                '<int:pk>/run-import/',
                self.admin_site.admin_view(self.run_import_view),
                name='store_suppliersource_run_import',
            ),
        ]
        return extra + urls

    @admin.display(description='Actions')
    def preview_button(self, obj):
        url = reverse('admin:store_suppliersource_preview', args=[obj.pk])
        return format_html('<a class="button" href="{}">Preview</a>', url)

    def preview_view(self, request, pk):
        from .importers.shopify import ShopifyImporter
        source = get_object_or_404(SupplierSource, pk=pk)
        try:
            importer = ShopifyImporter(source)
            preview = importer.preview(limit=50)
            return render(request, 'admin/store/suppliersource/preview.html', {
                'source': source,
                'preview': preview,
                'title': f'Preview: {source.name}',
                **self.admin_site.each_context(request),
            })
        except Exception as exc:
            messages.error(request, f'Preview failed: {exc}')
            return redirect('admin:store_suppliersource_changelist')

    def run_import_view(self, request, pk):
        from .importers.shopify import ShopifyImporter
        source = get_object_or_404(SupplierSource, pk=pk)
        try:
            importer = ShopifyImporter(source)
            result = importer.run(limit=None)
            source.last_synced_at = timezone.now()
            source.last_status = 'ok'
            source.last_error = ''
            source.save(update_fields=['last_synced_at', 'last_status', 'last_error'])
            messages.success(
                request,
                f'Imported {result["created"]} new, updated {result["updated"]}, '
                f'skipped {result["skipped"]}.'
            )
        except Exception as exc:
            source.last_status = 'error'
            source.last_error = str(exc)[:2000]
            source.save(update_fields=['last_status', 'last_error'])
            messages.error(request, f'Import failed: {exc}')
        return redirect('admin:store_suppliersource_changelist')


# ======================================================================
# Vehicle rentals
# ======================================================================

@admin.register(VehicleRental)
class VehicleRentalAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'product', 'user', 'pickup_date', 'return_date', 'total_days',
        'total_amount', 'status', 'created_at',
    )
    list_filter = ('status', 'pickup_date', 'created_at')
    search_fields = (
        'user__username', 'user__email',
        'product__name', 'delivery_phone', 'mpesa_receipt',
    )
    raw_id_fields = ('user', 'product', 'vendor')
    readonly_fields = (
        'total_days', 'subtotal', 'total_amount',
        'mpesa_checkout_request_id', 'mpesa_receipt',
        'mpesa_result_code', 'mpesa_result_desc',
        'created_at', 'paid_at', 'returned_at', 'cancelled_at',
        'license_preview',
    )
    actions = ('mark_returned_action', 'mark_cancelled_action')
    fieldsets = (
        (None, {
            'fields': ('product', 'vendor', 'user', 'status'),
        }),
        ('Booking window', {
            'fields': ('pickup_date', 'return_date', 'total_days'),
        }),
        ('Pricing', {
            'fields': ('daily_rate', 'rental_deposit', 'subtotal', 'total_amount'),
        }),
        ('Delivery', {
            'fields': (
                'delivery_full_name', 'delivery_phone',
                'delivery_address', 'delivery_notes',
            ),
        }),
        ('Customer verification', {
            'fields': ('driver_license', 'license_preview', 'customer_notes'),
        }),
        ('M-Pesa audit', {
            'fields': (
                'mpesa_phone', 'mpesa_checkout_request_id',
                'mpesa_receipt', 'mpesa_result_code', 'mpesa_result_desc',
            ),
            'classes': ('collapse',),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'paid_at', 'returned_at', 'cancelled_at'),
            'classes': ('collapse',),
        }),
    )

    @admin.display(description='License')
    def license_preview(self, obj):
        if not obj.driver_license:
            return '—'
        return format_html(
            '<a href="{0}" target="_blank">'
            '<img src="{0}" style="max-width:420px;border:1px solid #444"></a>',
            obj.driver_license.url,
        )

    @admin.action(description='Mark selected as RETURNED')
    def mark_returned_action(self, request, queryset):
        count = 0
        for r in queryset.filter(status__in=[
            VehicleRental.Status.CONFIRMED,
            VehicleRental.Status.ACTIVE,
        ]):
            r.mark_returned()
            count += 1
        self.message_user(request, f'{count} rental(s) marked returned.')

    @admin.action(description='Cancel selected rentals')
    def mark_cancelled_action(self, request, queryset):
        count = 0
        for r in queryset.filter(status__in=[
            VehicleRental.Status.PENDING,
            VehicleRental.Status.CONFIRMED,
        ]):
            r.mark_cancelled()
            count += 1
        self.message_user(request, f'{count} rental(s) cancelled.')
