from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from .cart import Cart
from .models import (
    Category, DeliveryAddress, Department, Product, ProductVariant, Promotion,
    VehicleRental,
)


_THEME_TO_HERO_TONE = {
    'green':   'green',
    'amber':   'gold',
    'blue':    'green',
    'crimson': 'orange',
    'violet':  'orange',
}


def _safe_next(request, fallback):
    nxt = request.POST.get('next') or request.GET.get('next')
    if nxt and url_has_allowed_host_and_scheme(
        nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return nxt
    return fallback


def _parse_quantity(request, default=1, minimum=1, maximum=999):
    try:
        q = int(request.POST.get('quantity', default))
    except (TypeError, ValueError):
        q = default
    return max(minimum, min(maximum, q))


class _HeroSlide:
    __slots__ = (
        'title', 'kicker', 'description', 'cta_label', 'cta_url',
        'tone', 'media_type', 'image', 'video_source', 'voucher_code',
        'product',
    )

    def __init__(self, product):
        self.product = product
        self.title = product.hero_headline or product.name
        self.kicker = product.hero_tagline or 'Featured'
        self.description = product.hero_description or product.description
        self.cta_label = 'Shop now'
        self.cta_url = product.get_absolute_url()

        theme = product.department.theme if product.department else 'green'
        self.tone = _THEME_TO_HERO_TONE.get(theme, 'orange')

        self.media_type = 'image'
        self.image = product.primary_image
        self.video_source = None
        self.voucher_code = ''


@require_GET
def home(request):
    department_slug = request.GET.get('department')
    category_slug = request.GET.get('category')
    search_query = request.GET.get('q', '').strip()

    departments = Department.objects.filter(is_active=True)
    categories = Category.objects.all()
    products = Product.objects.visible().select_related(
        'category', 'category__department', 'department'
    ).prefetch_related('variants', 'images')

    selected_department = None
    selected_category = None

    if department_slug:
        selected_department = get_object_or_404(
            Department, slug=department_slug, is_active=True
        )
        categories = categories.filter(department=selected_department)
        products = products.filter(department=selected_department)

    if category_slug:
        selected_category = get_object_or_404(Category, slug=category_slug)
        if selected_department and selected_category.department_id != selected_department.id:
            raise Http404(
                f'Category "{selected_category.name}" is not in the '
                f'"{selected_department.name}" department.'
            )
        if not selected_department and selected_category.department_id:
            selected_department = selected_category.department
        products = products.filter(category=selected_category)

    if selected_department:
        request.session['current_department_id'] = selected_department.id
    elif not department_slug and not category_slug:
        request.session.pop('current_department_id', None)

    if search_query:
        from django.db.models import Q
        products = products.filter(
            Q(name__icontains=search_query) | Q(description__icontains=search_query)
        )

    paginator = Paginator(products, 16)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    products = page_obj.object_list

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        grid_html = render_to_string(
            'store/_product_grid.html',
            {
                'products': products,
                'selected_department': selected_department,
                'page_obj': page_obj,
            },
            request=request,
        )
        if search_query:
            heading = f'Results for "{search_query}"'
        elif selected_category:
            heading = selected_category.name
        elif selected_department:
            heading = selected_department.name
        else:
            heading = 'All departments'
        return JsonResponse({
            'grid_html': grid_html,
            'heading': heading,
            'dept_theme': selected_department.theme if selected_department else '',
            'page_number': page_obj.number,
            'num_pages': page_obj.paginator.num_pages,
            'has_next': page_obj.has_next(),
            'next_page': page_obj.next_page_number() if page_obj.has_next() else None,
            'total_count': page_obj.paginator.count,
        })

    featured_products = (
        Product.objects
        .visible()
        .filter(is_featured=True)
        .select_related('category', 'department')
        .prefetch_related('images', 'variants')
        .order_by('-created_at')[:5]
    )
    hero_posters = [_HeroSlide(p) for p in featured_products]

    if not hero_posters:
        hero_posters = list(
            Promotion.objects
            .filter(is_active=True)
            .exclude(media_type='image', image='')
            .order_by('sort_order', '-created_at')[:5]
        )

    promotions = list(
        Promotion.objects
        .filter(is_active=True)
        .order_by('sort_order', '-created_at')[:6]
    )

    return render(request, 'store/home.html', {
        'categories': categories,
        'selected_category': selected_category,
        'products': products,
        'page_obj': page_obj,
        'hero_posters': hero_posters,
        'promotions': promotions,
        'search_query': search_query,
        'departments': departments,
        'selected_department': selected_department,
    })


@require_GET
def product_detail(request, slug):
    product = get_object_or_404(
        Product.objects.visible()
        .select_related('category', 'category__department', 'department', 'vendor')
        .prefetch_related('variants', 'images', 'offers'),
        slug=slug,
    )

    RELATED_LIMIT = 4
    related = []
    seen_ids = {product.pk}

    base_qs = (
        Product.objects.visible()
        .filter(is_rentable=product.is_rentable)
        .exclude(pk__in=seen_ids)
        .select_related('category', 'department')
        .prefetch_related('variants', 'images')
        .order_by('-created_at')
    )

    if product.category_id:
        cat_items = list(base_qs.filter(category_id=product.category_id)[:RELATED_LIMIT])
        related.extend(cat_items)
        seen_ids.update(p.pk for p in cat_items)

    if len(related) < RELATED_LIMIT and product.department_id:
        remaining = RELATED_LIMIT - len(related)
        dept_items = list(
            base_qs.filter(department_id=product.department_id)
            .exclude(pk__in=seen_ids)[:remaining]
        )
        related.extend(dept_items)
        seen_ids.update(p.pk for p in dept_items)

    if len(related) < RELATED_LIMIT and not product.department_id and product.category_id:
        remaining = RELATED_LIMIT - len(related)
        extras = list(
            base_qs.filter(category_id=product.category_id)
            .exclude(pk__in=seen_ids)[:remaining]
        )
        related.extend(extras)

    return render(request, 'store/product_detail.html', {
        'product': product,
        'related_products': related,
    })


@require_POST
def cart_add(request, product_id):
    cart = Cart(request)
    product = get_object_or_404(Product.objects.visible(), id=product_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    if not product.is_deliverable:
        message = (
            f'{product.name} cannot be added — online sale/delivery of this '
            'item is currently restricted.'
        )
        if is_ajax:
            return JsonResponse({'ok': False, 'message': message}, status=400)
        messages.error(request, message)
        return redirect(_safe_next(request, 'store:home'))

    if not product.is_available:
        message = f'{product.name} is not available right now.'
        if is_ajax:
            return JsonResponse({'ok': False, 'message': message}, status=400)
        messages.error(request, message)
        return redirect(_safe_next(request, product.get_absolute_url()))

    variant = None
    variant_id = request.POST.get('variant_id')
    if variant_id:
        variant = product.variants.filter(id=variant_id, is_active=True).first()
    if not variant and product.has_variants:
        variant = product.available_variants.first()

    quantity = _parse_quantity(request)
    available_stock = variant.stock if variant else product.stock
    if quantity > available_stock:
        message = f'Only {available_stock} left in stock.'
        if is_ajax:
            return JsonResponse({'ok': False, 'message': message}, status=400)
        messages.error(request, message)
        return redirect(_safe_next(request, product.get_absolute_url()))

    cart.add(product=product, variant=variant, quantity=quantity)
    label = f'{product.name} ({variant.size_label})' if variant else product.name

    if is_ajax:
        return JsonResponse({
            'ok': True,
            'message': f'Added {label} to your cart.',
            'cart_count': len(cart),
        })

    messages.success(request, f'Added {label} to your cart.')
    return redirect(_safe_next(request, 'store:cart_detail'))


@require_POST
def cart_remove(request, product_id):
    cart = Cart(request)
    product = get_object_or_404(Product, id=product_id)
    variant_id = request.POST.get('variant_id')
    variant = None
    if variant_id:
        variant = ProductVariant.objects.filter(id=variant_id, product=product).first()
    cart.remove(product, variant=variant)
    label = f'{product.name} ({variant.size_label})' if variant else product.name
    messages.info(request, f'Removed {label} from your cart.')
    return redirect('store:cart_detail')


@require_POST
def cart_update(request, product_id):
    cart = Cart(request)
    product = get_object_or_404(Product, id=product_id)
    variant_id = request.POST.get('variant_id')
    variant = None
    if variant_id:
        variant = ProductVariant.objects.filter(id=variant_id, product=product).first()
    quantity = _parse_quantity(request)
    cart.add(product=product, variant=variant, quantity=quantity, replace=True)
    return redirect('store:cart_detail')


@require_GET
def cart_detail(request):
    cart = Cart(request)
    return render(request, 'store/cart.html', {'cart': cart})


@login_required
@require_GET
def address_list(request):
    addresses = request.user.addresses.all()
    return render(request, 'store/address_list.html', {'addresses': addresses})


@login_required
def address_add(request):
    if request.method == 'POST':
        from django.db import transaction
        required = ('full_name', 'phone_number', 'building_name', 'street', 'area')
        missing = [f for f in required if not (request.POST.get(f) or '').strip()]
        if missing:
            messages.error(request, f'Please fill in: {", ".join(missing)}.')
            return render(request, 'store/address_form.html', status=400)

        is_default = request.POST.get('is_default') == 'on'

        try:
            with transaction.atomic():
                if is_default:
                    DeliveryAddress.objects.filter(
                        user=request.user, is_default=True
                    ).update(is_default=False)
                DeliveryAddress.objects.create(
                    user=request.user,
                    label=request.POST.get('label') or 'Home',
                    full_name=request.POST['full_name'].strip(),
                    phone_number=request.POST['phone_number'].strip(),
                    building_name=request.POST['building_name'].strip(),
                    apartment_number=request.POST.get('apartment_number', '').strip(),
                    floor=request.POST.get('floor', '').strip(),
                    street=request.POST['street'].strip(),
                    area=request.POST['area'].strip(),
                    city=request.POST.get('city') or 'Nairobi',
                    delivery_notes=request.POST.get('delivery_notes', '').strip(),
                    latitude=request.POST.get('latitude') or None,
                    longitude=request.POST.get('longitude') or None,
                    is_default=is_default,
                )
        except Exception as exc:
            messages.error(request, f'Could not save address: {exc}')
            return render(request, 'store/address_form.html', status=400)

        messages.success(request, 'Delivery address saved.')
        return redirect('store:address_list')
    return render(request, 'store/address_form.html')


# ======================================================================
# Vehicle rentals
# ======================================================================
@login_required
@require_GET
def rental_checkout(request, product_id):
    from .forms import VehicleRentalForm

    product = get_object_or_404(
        Product.objects.visible().select_related('vendor', 'department', 'category'),
        id=product_id, is_rentable=True,
    )

    if not product.daily_rate:
        messages.error(request, 'This vehicle has no daily rate set. Contact support.')
        return redirect(product.get_absolute_url())

    initial = {}
    default_addr = (
        request.user.addresses.filter(is_default=True).first()
        or request.user.addresses.first()
    )
    if default_addr:
        initial.update({
            'delivery_full_name': default_addr.full_name,
            'delivery_phone': default_addr.phone_number,
            'delivery_address': default_addr.as_text(),
        })

    form = VehicleRentalForm(product=product, user=request.user, initial=initial)

    return render(request, 'store/rental_checkout.html', {
        'product': product,
        'form': form,
        'min_days': product.minimum_days or 1,
    })


@login_required
@require_POST
def rental_start_payment(request, product_id):
    from payments.mpesa import MpesaError, normalize_phone, stk_push
    from .forms import VehicleRentalForm

    product = get_object_or_404(
        Product.objects.visible().select_related('vendor'),
        id=product_id, is_rentable=True,
    )

    if not product.daily_rate:
        messages.error(request, 'This vehicle has no daily rate set.')
        return redirect(product.get_absolute_url())

    form = VehicleRentalForm(
        request.POST, request.FILES, product=product, user=request.user,
    )

    if not form.is_valid():
        return render(request, 'store/rental_checkout.html', {
            'product': product,
            'form': form,
            'min_days': product.minimum_days or 1,
        })

    pickup = form.cleaned_data['pickup_date']
    ret = form.cleaned_data['return_date']

    if VehicleRental.has_overlap(product, pickup, ret):
        messages.error(
            request,
            'Sorry — someone just booked this vehicle for those dates. '
            'Please pick a different window.'
        )
        return redirect('store:rental_checkout', product_id=product.id)

    total_days = (ret - pickup).days
    daily_rate = product.daily_rate
    deposit = product.rental_deposit or 0
    subtotal = daily_rate * total_days
    total = subtotal + deposit

    rental = VehicleRental.objects.create(
        user=request.user,
        product=product,
        vendor=product.vendor,
        pickup_date=pickup,
        return_date=ret,
        total_days=total_days,
        daily_rate=daily_rate,
        rental_deposit=deposit,
        subtotal=subtotal,
        total_amount=total,
        delivery_full_name=form.cleaned_data['delivery_full_name'],
        delivery_phone=form.cleaned_data['delivery_phone'],
        delivery_address=form.cleaned_data['delivery_address'],
        delivery_notes=form.cleaned_data.get('delivery_notes', ''),
        driver_license=form.cleaned_data['driver_license'],
        customer_notes=form.cleaned_data.get('customer_notes', ''),
        status=VehicleRental.Status.PENDING,
    )

    phone = form.cleaned_data['delivery_phone']

    try:
        response = stk_push(
            phone_number=phone,
            amount=int(total),
            account_reference=f'RENTAL{rental.id}',
            transaction_desc=f'{product.name} rental ({total_days}d)',
        )
    except MpesaError as exc:
        rental.mark_failed(code='init', desc=str(exc))
        messages.error(request, f'Could not start the payment: {exc}')
        return redirect('store:rental_checkout', product_id=product.id)

    checkout_id = response.get('CheckoutRequestID', '')
    if checkout_id:
        rental.mpesa_checkout_request_id = checkout_id
        rental.mpesa_phone = normalize_phone(phone)
        rental.save(update_fields=['mpesa_checkout_request_id', 'mpesa_phone'])

    messages.info(
        request,
        f'An M-Pesa prompt for KES {total:,} has been sent to {phone}. '
        f'Enter your PIN to confirm the booking.'
    )
    return redirect('store:rental_success', booking_id=rental.id)


@login_required
@require_GET
def rental_success(request, booking_id):
    rental = get_object_or_404(
        VehicleRental.objects.select_related('product'),
        id=booking_id, user=request.user,
    )
    return render(request, 'store/rental_success.html', {'rental': rental})


@login_required
@require_GET
def rental_status(request, booking_id):
    rental = get_object_or_404(VehicleRental, id=booking_id, user=request.user)
    return JsonResponse({
        'status': rental.status,
        'paid': rental.status == VehicleRental.Status.CONFIRMED,
        'failed': rental.status in (
            VehicleRental.Status.FAILED,
            VehicleRental.Status.CANCELLED,
        ),
        'receipt': rental.mpesa_receipt,
    })


@require_GET
def rental_availability(request, product_id):
    product = get_object_or_404(Product, id=product_id, is_rentable=True)

    try:
        pickup = datetime.strptime(request.GET.get('pickup', ''), '%Y-%m-%d').date()
        ret = datetime.strptime(request.GET.get('return', ''), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Invalid dates'}, status=400)

    if ret <= pickup:
        return JsonResponse({'available': False, 'reason': 'Return date must be after pickup.'})

    min_days = product.minimum_days or 1
    days = (ret - pickup).days
    if days < min_days:
        return JsonResponse({
            'available': False,
            'reason': f'Minimum rental is {min_days} day{"s" if min_days != 1 else ""}.',
        })

    if VehicleRental.has_overlap(product, pickup, ret):
        return JsonResponse({
            'available': False,
            'reason': 'This vehicle is already booked for those dates.',
        })

    subtotal = product.daily_rate * days
    deposit = product.rental_deposit or 0
    return JsonResponse({
        'available': True,
        'days': days,
        'subtotal': str(subtotal),
        'deposit': str(deposit),
        'total': str(subtotal + deposit),
    })


@login_required
@require_GET
def my_rentals(request):
    rentals = (
        VehicleRental.objects
        .filter(user=request.user)
        .select_related('product', 'vendor')
        .order_by('-created_at')
    )
    return render(request, 'store/my_rentals.html', {'rentals': rentals})





@require_GET
def product_list(request):
    """The full product listing page. Renders a shell that the JS
    hydrates; falls back to the server-rendered grid if JS is off."""
    departments = Department.objects.filter(is_active=True)
    categories = Category.objects.select_related('department').order_by('name')

    # Initial products — same query params as the API, so bookmarked URLs
    # with filters land on a page with the right grid already rendered.
    qs = (
        Product.objects.visible()
        .select_related('category', 'department', 'vendor')
        .prefetch_related('variants', 'images')
        .order_by('-created_at')
    )
    department_slug = request.GET.get('department')
    category_slug = request.GET.get('category')
    search_query = request.GET.get('q', '').strip()

    selected_department = None
    selected_category = None

    if department_slug:
        selected_department = Department.objects.filter(
            slug=department_slug, is_active=True
        ).first()
        if selected_department:
            qs = qs.filter(department=selected_department)

    if category_slug:
        selected_category = Category.objects.filter(slug=category_slug).first()
        if selected_category:
            qs = qs.filter(category=selected_category)

    if search_query:
        from django.db.models import Q
        qs = qs.filter(
            Q(name__icontains=search_query) |
            Q(description__icontains=search_query)
        )

    paginator = Paginator(qs, 24)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'store/product_list.html', {
        'products': page_obj.object_list,
        'page_obj': page_obj,
        'departments': departments,
        'categories': categories,
        'selected_department': selected_department,
        'selected_category': selected_category,
        'search_query': search_query,
    })
