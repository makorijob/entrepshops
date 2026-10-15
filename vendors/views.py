from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.db import models, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_GET, require_POST

from marketing.models import WithdrawalRequest
from store.models import Category, Department, Product

from . import ai
from .forms import VendorProductForm, VendorProfileForm, VendorWithdrawalForm
from .models import Vendor, VendorSubscription


SUBSCRIPTION_FEE_KES = getattr(settings, 'VENDOR_SUBSCRIPTION_FEE_KES', 5000)
SUBSCRIPTION_DAYS = getattr(settings, 'VENDOR_SUBSCRIPTION_DAYS', 30)


# ======================================================================
# Start / manage vendor
# ======================================================================
@login_required
@require_GET
def become_vendor(request):
    """The pre-vendor pitch. If the user already has a Vendor row, send
    them onward: dashboard if paid, subscribe page if not."""
    if hasattr(request.user, 'vendor_profile'):
        vendor = request.user.vendor_profile
        if vendor.has_active_subscription or vendor.on_trial:
            return redirect('vendors:dashboard')
        return redirect('vendors:subscribe')

    return render(request, 'vendors/become_vendor.html', {
        'subscription_fee': SUBSCRIPTION_FEE_KES,
        'subscription_days': SUBSCRIPTION_DAYS,
    })


@login_required
@require_POST
def start_signup(request):
    """Create the Vendor row (unpaid) and send the user to the payment
    page. No trial, no free listing — the subscription must be paid
    before any product can be added."""
    if hasattr(request.user, 'vendor_profile'):
        return redirect('vendors:subscribe')

    business_name = (request.POST.get('business_name') or '').strip()
    if not business_name:
        messages.error(request, 'Please enter your business name.')
        return redirect('vendors:become_vendor')

    phone_number = (request.POST.get('phone_number') or '').strip()

    Vendor.objects.create(
        user=request.user,
        business_name=business_name[:150],
        phone_number=phone_number[:20],
    )

    messages.info(
        request,
        'Business created. Complete your subscription to start listing products.'
    )
    return redirect('vendors:subscribe')


@login_required
@require_GET
def subscribe(request):
    """Subscription payment page."""
    vendor = get_object_or_404(Vendor, user=request.user)

    return render(request, 'vendors/subscribe.html', {
        'vendor': vendor,
        'subscription_fee': SUBSCRIPTION_FEE_KES,
        'subscription_days': SUBSCRIPTION_DAYS,
        'recent_subscriptions': vendor.subscriptions.all()[:5],
    })


@login_required
@require_POST
def start_subscription_payment(request):
    """Initiate an M-Pesa STK push for the vendor subscription fee.
    Creates a PENDING VendorSubscription and records the checkout id.
    The M-Pesa callback flips it to PAID."""
    from payments.mpesa import MpesaError, normalize_phone, stk_push

    vendor = get_object_or_404(Vendor, user=request.user)

    phone = (request.POST.get('phone_number') or '').strip()
    if not phone:
        messages.error(request, 'Enter the M-Pesa number to charge.')
        return redirect('vendors:subscribe')

    subscription = VendorSubscription.objects.create(
        vendor=vendor,
        amount=SUBSCRIPTION_FEE_KES,
        period_days=SUBSCRIPTION_DAYS,
        mpesa_phone=normalize_phone(phone),
    )

    try:
        response = stk_push(
            phone_number=phone,
            amount=SUBSCRIPTION_FEE_KES,
            account_reference=f'VENDORSUB{subscription.id}',
            transaction_desc=f'Vendor subscription ({SUBSCRIPTION_DAYS}d)',
        )
    except MpesaError as exc:
        subscription.mark_failed(code='init', desc=str(exc))
        messages.error(request, f'Could not start the payment: {exc}')
        return redirect('vendors:subscribe')

    checkout_id = response.get('CheckoutRequestID', '')
    if checkout_id:
        subscription.mpesa_checkout_request_id = checkout_id
        subscription.save(update_fields=['mpesa_checkout_request_id'])

    messages.success(
        request,
        'An M-Pesa prompt has been sent to your phone. Enter your PIN to '
        'complete the payment. Your subscription will activate automatically.'
    )
    return redirect('vendors:subscribe')


@login_required
@require_GET
def subscription_status(request):
    """Polled by the subscribe page JS to check if payment landed."""
    vendor = get_object_or_404(Vendor, user=request.user)
    latest = vendor.subscriptions.first()

    return JsonResponse({
        'active': vendor.has_active_subscription,
        'expires_at': (
            vendor.subscription_expires_at.isoformat()
            if vendor.subscription_expires_at else None
        ),
        'days_left': vendor.subscription_days_left,
        'latest_status': latest.status if latest else None,
        'latest_paid_at': (
            latest.paid_at.isoformat() if latest and latest.paid_at else None
        ),
    })


def send_welcome_email(vendor):
    """Kept for compatibility — fired after the first successful
    subscription payment rather than at signup."""
    user = vendor.user
    if not user.email:
        return
    subject = 'Welcome to Entrep Shop — your vendor subscription is active'
    body = render_to_string('vendors/emails/welcome.txt', {
        'user': user,
        'vendor': vendor,
        'subscription_days': SUBSCRIPTION_DAYS,
        'site_name': 'Entrep - Shop',
    })
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)


# ======================================================================
# Dashboard
# ======================================================================
@login_required
@require_GET
def dashboard(request):
    vendor = get_object_or_404(Vendor, user=request.user)
    products = vendor.products.select_related('category').order_by('-created_at')

    return render(request, 'vendors/dashboard.html', {
        'vendor': vendor,
        'products': products,
        'subscription_fee': SUBSCRIPTION_FEE_KES,
    })


@login_required
def edit_profile(request):
    vendor = get_object_or_404(Vendor, user=request.user)

    if request.method == 'POST':
        form = VendorProfileForm(request.POST)
        if form.is_valid():
            vendor.business_name = form.cleaned_data['business_name'][:150]
            vendor.phone_number = form.cleaned_data['phone_number'][:20]
            vendor.description = form.cleaned_data['description']
            vendor.save(update_fields=['business_name', 'phone_number', 'description'])
            messages.success(request, 'Profile updated.')
            return redirect('vendors:dashboard')
    else:
        form = VendorProfileForm(initial={
            'business_name': vendor.business_name,
            'phone_number': vendor.phone_number,
            'description': vendor.description,
        })

    return render(request, 'vendors/edit_profile.html', {
        'vendor': vendor,
        'form': form,
    })


# ======================================================================
# Public vendor shop (no login required)
# ======================================================================
@require_GET
def public_profile(request, vendor_id):
    """Public-facing vendor shop page. Shows the vendor's business name,
    description, and every live product they've listed."""
    from django.core.paginator import Paginator

    vendor = get_object_or_404(Vendor, id=vendor_id, is_approved=True)

    products = (
        vendor.products
        .filter(is_approved=True, is_active=True)
        .exclude(availability=Product.Availability.DISCONTINUED)
        .select_related('category', 'department')
        .prefetch_related('variants', 'images')
        .order_by('-created_at')
    )

    paginator = Paginator(products, 24)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'vendors/public_profile.html', {
        'vendor': vendor,
        'products': page_obj.object_list,
        'page_obj': page_obj,
    })


# ======================================================================
# Withdrawals
# ======================================================================
@login_required
def withdraw(request):
    vendor = get_object_or_404(Vendor, user=request.user)

    available = vendor.balance
    pending_total = (
        WithdrawalRequest.objects
        .filter(
            vendor=vendor,
            status__in=[
                WithdrawalRequest.Status.PENDING,
                WithdrawalRequest.Status.APPROVED,
            ],
        )
        .aggregate(s=models.Sum('amount'))['s']
        or Decimal('0.00')
    )

    if available <= Decimal('0.00'):
        messages.info(request, 'You have no balance available to withdraw yet.')
        return redirect('vendors:dashboard')

    initial = {
        'phone_number': vendor.phone_number,
        'amount': available,
    }

    if request.method == 'POST':
        form = VendorWithdrawalForm(request.POST, max_amount=available)
        if form.is_valid():
            amount = form.cleaned_data['amount']
            phone = form.cleaned_data['phone_number']

            with transaction.atomic():
                locked = Vendor.objects.select_for_update().get(pk=vendor.pk)
                if amount > locked.balance:
                    messages.error(
                        request,
                        f'Your withdrawable balance is KES {locked.balance:.2f}.'
                    )
                    return render(request, 'vendors/withdraw.html', {
                        'vendor': vendor,
                        'form': form,
                        'available': locked.balance,
                        'pending_total': pending_total,
                    })

                locked.balance = locked.balance - amount
                locked.save(update_fields=['balance'])

                wr = WithdrawalRequest.objects.create(
                    user=request.user,
                    vendor=vendor,
                    phone_number=phone,
                    amount=amount,
                )

            try:
                from marketing.views import send_withdrawal_requested_email
                send_withdrawal_requested_email(wr)
            except Exception:
                pass

            messages.success(
                request,
                f'Withdrawal of KES {amount} is now in progress. '
                f'We will send it to {phone} within 24 hours.'
            )
            return redirect('vendors:withdrawal_history')
    else:
        form = VendorWithdrawalForm(max_amount=available, initial=initial)

    return render(request, 'vendors/withdraw.html', {
        'vendor': vendor,
        'form': form,
        'available': available,
        'pending_total': pending_total,
    })


@login_required
@require_GET
def withdrawal_history(request):
    vendor = get_object_or_404(Vendor, user=request.user)
    withdrawals = (
        WithdrawalRequest.objects
        .filter(vendor=vendor)
        .order_by('-requested_at')
    )
    return render(request, 'vendors/withdrawal_history.html', {
        'vendor': vendor,
        'withdrawals': withdrawals,
        'available': vendor.balance,
    })


# ======================================================================
# Product upload
# ======================================================================
@login_required
@require_GET
def product_list(request):
    vendor = get_object_or_404(Vendor, user=request.user)
    products = vendor.products.select_related('category').order_by('-created_at')
    return render(request, 'vendors/product_list.html', {
        'vendor': vendor,
        'products': products,
    })


@login_required
def product_create(request):
    import logging
    log = logging.getLogger(__name__)

    vendor = get_object_or_404(Vendor, user=request.user)

    # Admins bypass both the payment and the approval gates.
    if not request.user.is_staff:
        if not vendor.has_active_subscription:
            messages.error(
                request,
                'An active subscription is required to add products. '
                f'Subscribe for KES {SUBSCRIPTION_FEE_KES} to start selling.'
            )
            return redirect('vendors:subscribe')
        if not vendor.is_approved:
            messages.error(
                request,
                'Your vendor account is suspended. Contact support.'
            )
            return redirect('vendors:dashboard')

    if request.method == 'POST':
        form = VendorProductForm(request.POST, request.FILES, vendor=vendor)
        if form.is_valid():
            product = form.save(commit=False)
            product.vendor = vendor

            if not product.department_id and product.category_id:
                product.department = product.category.department

            # Admins auto-approve. Vendors follow the first-3-reviewed rule.
            if request.user.is_staff:
                product.is_approved = True
            else:
                product.is_approved = not vendor.requires_review

            base_slug = slugify(product.name)[:150] or 'product'
            slug = base_slug
            n = 1
            while Product.objects.filter(slug=slug).exists():
                n += 1
                slug = f'{base_slug}-{n}'[:160]
            product.slug = slug

            product.save()

            if product.is_approved:
                messages.success(request, f'"{product.name}" is now live in your shop.')
            else:
                messages.success(
                    request,
                    f'"{product.name}" has been submitted. Our team reviews the '
                    f'first 3 products — you\'ll get an email when it\'s approved.'
                )
            return redirect('vendors:product_list')

        # Form is invalid — log every error so it shows in the terminal.
        log.warning('=' * 60)
        log.warning('Product form INVALID for vendor=%s user=%s', vendor.pk, request.user.pk)
        log.warning('POST data keys: %s', list(request.POST.keys()))
        log.warning('FILES keys: %s', list(request.FILES.keys()))
        for field, errs in form.errors.items():
            log.warning('  FIELD %r -> %s', field, [str(e) for e in errs])
        log.warning('Non-field errors: %s', form.non_field_errors())
        log.warning('=' * 60)
    else:
        form = VendorProductForm(vendor=vendor)

    all_categories = Category.objects.select_related('department').order_by('name')

    return render(request, 'vendors/product_form.html', {
        'vendor': vendor,
        'form': form,
        'all_categories': all_categories,
        'mode': 'create',
    })


@login_required
def product_edit(request, product_id):
    vendor = get_object_or_404(Vendor, user=request.user)
    product = get_object_or_404(Product, id=product_id, vendor=vendor)

    if request.method == 'POST':
        form = VendorProductForm(request.POST, request.FILES, vendor=vendor, instance=product)
        if form.is_valid():
            form.save()
            messages.success(request, 'Product updated.')
            return redirect('vendors:product_list')
    else:
        form = VendorProductForm(vendor=vendor, instance=product)

    all_categories = Category.objects.select_related('department').order_by('name')

    return render(request, 'vendors/product_form.html', {
        'vendor': vendor,
        'form': form,
        'product': product,
        'all_categories': all_categories,
        'mode': 'edit',
    })


@login_required
@require_POST
def product_delete(request, product_id):
    vendor = get_object_or_404(Vendor, user=request.user)
    product = get_object_or_404(Product, id=product_id, vendor=vendor)
    product.delete()
    messages.success(request, 'Product removed.')
    return redirect('vendors:product_list')


# ======================================================================
# AI suggest
# ======================================================================
@login_required
@require_POST
def ai_suggest(request):
    if not hasattr(request.user, 'vendor_profile'):
        return JsonResponse({'ok': False, 'error': 'Not a vendor.'}, status=403)

    text = (request.POST.get('text') or '').strip()
    image = request.FILES.get('image')

    if not text and not image:
        return JsonResponse(
            {'ok': False, 'error': 'Type something or upload an image first.'},
            status=400,
        )

    result = ai.suggest_product(text=text, image_file=image)
    if not result:
        return JsonResponse(
            {
                'ok': False,
                'error': 'The AI could not generate a suggestion right now. '
                         'Please fill in the fields manually.',
            },
            status=200,
        )

    dept_name = result['department'][:100]
    department = Department.objects.filter(name__iexact=dept_name).first()
    if not department:
        department = Department.objects.create(
            name=dept_name,
            slug=slugify(dept_name)[:110] or 'general',
            theme='amber',
            unit_kind='none',
            icon_kind='star',
            is_active=True,
        )

    cat_name = result['category'][:100]
    category = Category.objects.filter(
        name__iexact=cat_name, department=department,
    ).first()
    if not category:
        category = Category.objects.filter(name__iexact=cat_name).first()
        if not category:
            base_slug = slugify(cat_name)[:110] or 'general'
            slug = base_slug
            n = 1
            while Category.objects.filter(slug=slug).exists():
                n += 1
                slug = f'{base_slug}-{n}'[:110]
            category = Category.objects.create(
                name=cat_name,
                slug=slug,
                department=department,
            )

    return JsonResponse({
        'ok': True,
        'title': result['title'],
        'description': result['description'],
        'department_id': department.id,
        'department_name': department.name,
        'category_id': category.id,
        'category_name': category.name,
        'price_low': result.get('price_low'),
        'price_high': result.get('price_high'),
        'price_notes': result.get('price_notes', ''),
    })
