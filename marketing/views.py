import hashlib
from datetime import timedelta
from decimal import Decimal

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import F, Sum
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import (
    MarketingCampaign,
    MarketingPayout,
    MarketingProof,
    MarketingShare,
    MarketingView,
    MarketerSubscription,
    WithdrawalRequest,
    has_active_marketing_subscription,
    withdrawable_balance,
)


# ======================================================================
# Helpers
# ======================================================================
def _fingerprint(request, campaign):
    raw = '|'.join([
        request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', '')),
        request.META.get('HTTP_USER_AGENT', ''),
        str(campaign.id),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


def _marketer_is_exempt(user):
    """Admins bypass the activation paywall entirely."""
    return bool(user.is_authenticated and user.is_staff)


class ProofUploadForm(forms.ModelForm):
    class Meta:
        model = MarketingProof
        fields = ('screenshot', 'reported_views', 'note')
        widgets = {'note': forms.Textarea(attrs={'rows': 3})}


class WithdrawalForm(forms.Form):
    phone_number = forms.RegexField(
        regex=r'^2547\d{8}$',
        error_messages={
            'invalid': 'Enter a Safaricom number in the format 2547XXXXXXXX '
                       '(e.g. 254712345678).'
        },
        label='M-Pesa phone number',
        widget=forms.TextInput(attrs={'placeholder': '254712345678'}),
    )
    amount = forms.DecimalField(
        min_value=100, max_digits=10, decimal_places=2,
        label='Amount (KES)',
        widget=forms.NumberInput(attrs={'step': '1', 'min': '100'}),
    )

    def __init__(self, *args, max_amount=0, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_amount = Decimal(str(max_amount))
        self.fields['amount'].widget.attrs['max'] = str(self.max_amount)
        self.fields['amount'].help_text = (
            f'Available to withdraw: KES {self.max_amount:.2f}'
        )

    def clean_amount(self):
        amount = self.cleaned_data['amount']
        if amount < 100:
            raise forms.ValidationError('Minimum withdrawal is KES 100.')
        if amount > self.max_amount:
            raise forms.ValidationError(
                f'Your withdrawable balance is KES {self.max_amount:.2f}.'
            )
        return amount


# ======================================================================
# Campaign + share views
# ======================================================================
@require_GET
def campaign_list(request):
    campaign = (
        MarketingCampaign.objects
        .filter(is_active=True, starts_at__lte=timezone.now())
        .order_by('-created_at')
        .first()
    )
    if campaign is None:
        return render(request, 'marketing/list.html', {'campaign': None})

    share = None
    if request.user.is_authenticated:
        share = MarketingShare.objects.filter(user=request.user, campaign=campaign).first()

    sub = None
    if request.user.is_authenticated:
        sub = getattr(request.user, 'marketer_subscription', None)

    return render(request, 'marketing/list.html', {
        'campaign': campaign,
        'share': share,
        'marketer_sub': sub,
        'marketer_active': (
            _marketer_is_exempt(request.user)
            or has_active_marketing_subscription(request.user)
        ),
        'activation_fee': settings.MARKETER_ACTIVATION_FEE_KES,
    })


@login_required
@require_POST
def create_share(request, campaign_id):
    campaign = get_object_or_404(MarketingCampaign, id=campaign_id)
    if not campaign.is_live:
        messages.error(request, 'This campaign is no longer active.')
        return redirect('marketing:list')

    # ---- Paywall ----
    if not _marketer_is_exempt(request.user) and not has_active_marketing_subscription(request.user):
        messages.error(
            request,
            f'Activate your WhatsApp Earn account for KES '
            f'{settings.MARKETER_ACTIVATION_FEE_KES} to start sharing.'
        )
        return redirect('marketing:subscribe')

    share, created = MarketingShare.objects.get_or_create(
        user=request.user, campaign=campaign,
    )
    if created:
        messages.success(request, 'Your share link is ready — copy it and post it to WhatsApp.')
    else:
        messages.info(request, 'Here is your existing share link for this campaign.')
    return redirect('marketing:list')


@require_GET
def redirect_view(request, code):
    share = get_object_or_404(MarketingShare, code=code)
    campaign = share.campaign

    if campaign.is_live:
        fp = _fingerprint(request, campaign)
        try:
            with transaction.atomic():
                _, created = MarketingView.objects.get_or_create(
                    share=share, fingerprint=fp,
                    defaults={
                        'user_agent': request.META.get('HTTP_USER_AGENT', '')[:300],
                        'referer': request.META.get('HTTP_REFERER', '')[:300],
                    },
                )
                if created:
                    MarketingShare.objects.filter(pk=share.pk).update(
                        views=F('views') + 1,
                        earnings=F('earnings') + campaign.payout_per_view,
                        last_view_at=timezone.now(),
                    )
                    payout, _ = MarketingPayout.objects.get_or_create(
                        user=share.user, campaign=campaign,
                    )
                    MarketingPayout.objects.filter(pk=payout.pk).update(
                        views=F('views') + 1,
                        amount=F('amount') + campaign.payout_per_view,
                    )
        except Exception:
            pass

    return HttpResponseRedirect(campaign.landing_url)


# ======================================================================
# Marketer activation
# ======================================================================
@login_required
@require_GET
def subscribe(request):
    """Activation page — pitch + pay button + current status."""
    sub, _ = MarketerSubscription.objects.get_or_create(
        user=request.user,
        defaults={
            'amount': settings.MARKETER_ACTIVATION_FEE_KES,
            'period_days': settings.MARKETER_ACTIVATION_DAYS,
        },
    )

    return render(request, 'marketing/subscribe.html', {
        'sub': sub,
        'activation_fee': settings.MARKETER_ACTIVATION_FEE_KES,
        'activation_days': settings.MARKETER_ACTIVATION_DAYS,
        'is_active': sub.is_active or _marketer_is_exempt(request.user),
    })


@login_required
@require_POST
def start_activation_payment(request):
    """Fire an STK push for the activation fee."""
    from payments.mpesa import MpesaError, normalize_phone, stk_push

    phone = (request.POST.get('phone_number') or '').strip()
    if not phone:
        messages.error(request, 'Enter the M-Pesa number to charge.')
        return redirect('marketing:subscribe')

    sub, _ = MarketerSubscription.objects.get_or_create(
        user=request.user,
        defaults={
            'amount': settings.MARKETER_ACTIVATION_FEE_KES,
            'period_days': settings.MARKETER_ACTIVATION_DAYS,
        },
    )

    # Reset to a fresh PENDING attempt — user may be re-paying after expiry.
    sub.status = MarketerSubscription.Status.PENDING
    sub.amount = settings.MARKETER_ACTIVATION_FEE_KES
    sub.period_days = settings.MARKETER_ACTIVATION_DAYS
    sub.mpesa_phone = normalize_phone(phone)
    sub.mpesa_checkout_request_id = ''
    sub.mpesa_receipt = ''
    sub.mpesa_result_code = ''
    sub.mpesa_result_desc = ''
    sub.save()

    try:
        response = stk_push(
            phone_number=phone,
            amount=settings.MARKETER_ACTIVATION_FEE_KES,
            account_reference=f'MKTRSUB{sub.id}',
            transaction_desc='WhatsApp Earn activation',
        )
    except MpesaError as exc:
        sub.mark_failed(code='init', desc=str(exc))
        messages.error(request, f'Could not start the payment: {exc}')
        return redirect('marketing:subscribe')

    checkout_id = response.get('CheckoutRequestID', '')
    if checkout_id:
        sub.mpesa_checkout_request_id = checkout_id
        sub.save(update_fields=['mpesa_checkout_request_id'])

    messages.success(
        request,
        'An M-Pesa prompt has been sent to your phone. Enter your PIN to '
        'activate your account — this takes a few seconds.'
    )
    return redirect('marketing:subscribe')


@login_required
@require_GET
def activation_status(request):
    """Polled by the subscribe page JS."""
    sub = getattr(request.user, 'marketer_subscription', None)
    active = _marketer_is_exempt(request.user) or (sub and sub.is_active)

    return JsonResponse({
        'active': bool(active),
        'days_left': sub.days_left if sub else 0,
        'expires_at': sub.expires_at.isoformat() if (sub and sub.expires_at) else None,
        'latest_status': sub.status if sub else None,
    })


# ======================================================================
# Earnings
# ======================================================================
@login_required
@require_GET
def my_earnings(request):
    shares = (
        MarketingShare.objects
        .filter(user=request.user)
        .select_related('campaign')
        .order_by('-created_at')
    )
    totals = shares.aggregate(
        views=Sum('views'),
        earnings=Sum('earnings'),
    )
    credited_views = totals['views'] or 0
    credited_amount = totals['earnings'] or 0

    pending = (
        MarketingProof.objects
        .filter(share__user=request.user, status=MarketingProof.Status.PENDING)
        .select_related('share__campaign')
        .order_by('-uploaded_at')
    )
    pending_views = 0
    pending_total = Decimal('0.00')
    for p in pending:
        pending_views += p.reported_views
        pending_total += p.reported_views * p.share.campaign.payout_per_view

    total_views_all = credited_views + pending_views
    total_earned_all = credited_amount + pending_total

    recent_withdrawals = (
        WithdrawalRequest.objects
        .filter(user=request.user)
        .order_by('-requested_at')[:5]
    )

    in_progress_total = (
        WithdrawalRequest.objects
        .filter(
            user=request.user,
            status__in=[
                WithdrawalRequest.Status.PENDING,
                WithdrawalRequest.Status.APPROVED,
            ],
        )
        .aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    )

    sub = getattr(request.user, 'marketer_subscription', None)

    return render(request, 'marketing/earnings.html', {
        'shares': shares,
        'credited_views': credited_views,
        'credited_amount': credited_amount,
        'pending_proofs': pending,
        'pending_views': pending_views,
        'pending_total': pending_total,
        'total_views': total_views_all,
        'total_earnings': total_earned_all,
        'balance': withdrawable_balance(request.user),
        'in_progress_total': in_progress_total,
        'recent_withdrawals': recent_withdrawals,
        'marketer_sub': sub,
        'marketer_active': (
            _marketer_is_exempt(request.user)
            or has_active_marketing_subscription(request.user)
        ),
        'activation_fee': settings.MARKETER_ACTIVATION_FEE_KES,
    })


# ======================================================================
# Proof upload + emails
# ======================================================================
@login_required
def upload_proof(request, share_id):
    share = get_object_or_404(MarketingShare, id=share_id, user=request.user)

    # ---- Paywall ----
    if not _marketer_is_exempt(request.user) and not has_active_marketing_subscription(request.user):
        messages.error(
            request,
            f'Activate your WhatsApp Earn account for KES '
            f'{settings.MARKETER_ACTIVATION_FEE_KES} to upload proofs.'
        )
        return redirect('marketing:subscribe')

    if request.method == 'POST':
        form = ProofUploadForm(request.POST, request.FILES)
        if form.is_valid():
            proof = form.save(commit=False)
            proof.share = share
            proof.payout_due_at = timezone.now() + timedelta(hours=24)
            proof.save()

            try:
                send_proof_received_email(proof)
                proof.confirmation_sent_at = timezone.now()
                proof.save(update_fields=['confirmation_sent_at'])
            except Exception:
                pass

            messages.success(
                request,
                "Screenshot received. We'll credit your earnings within 24 hours. "
                "Check your email for confirmation."
            )
            return redirect('marketing:earnings')
    else:
        form = ProofUploadForm()

    return render(request, 'marketing/upload_proof.html', {
        'share': share,
        'form': form,
    })


def send_proof_received_email(proof):
    user = proof.share.user
    if not user.email:
        return
    subject = f'We received your screenshot — {proof.share.campaign.title}'
    body = render_to_string('marketing/emails/proof_received.txt', {
        'user': user,
        'proof': proof,
        'share': proof.share,
        'campaign': proof.share.campaign,
        'payout_per_view': proof.share.campaign.payout_per_view,
        'estimated_amount': proof.reported_views * proof.share.campaign.payout_per_view,
        'site_name': 'Entrep - Shop',
    })
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)


def send_proof_congrats_email(proof):
    user = proof.share.user
    if not user.email:
        return
    subject = f'You earned KES {proof.credited_amount} — {proof.share.campaign.title}'
    body = render_to_string('marketing/emails/proof_congrats.txt', {
        'user': user,
        'proof': proof,
        'share': proof.share,
        'campaign': proof.share.campaign,
        'credited_views': proof.reported_views,
        'credited_amount': proof.credited_amount,
        'total_earnings': proof.share.earnings,
        'site_name': 'Entrep - Shop',
    })
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)


def send_withdrawal_requested_email(wr):
    user = wr.user
    if not user.email:
        return
    subject = f'Withdrawal in progress — KES {wr.amount}'
    body = render_to_string('marketing/emails/withdrawal_requested.txt', {
        'user': user,
        'wr': wr,
        'site_name': 'Entrep - Shop',
    })
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)


def send_withdrawal_paid_email(wr):
    user = wr.user
    if not user.email:
        return
    subject = f'KES {wr.amount} sent to {wr.phone_number}'
    body = render_to_string('marketing/emails/withdrawal_paid.txt', {
        'user': user,
        'wr': wr,
        'site_name': 'Entrep - Shop',
    })
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)


# ======================================================================
# 24-hour proof sweep
# ======================================================================
def process_due_proofs():
    now = timezone.now()
    due = (
        MarketingProof.objects
        .filter(status=MarketingProof.Status.PENDING, payout_due_at__lte=now)
        .select_related('share__campaign', 'share__user')
    )

    processed = 0
    total = Decimal('0.00')

    for proof in due:
        amount = proof.reported_views * proof.share.campaign.payout_per_view

        with transaction.atomic():
            MarketingShare.objects.filter(pk=proof.share_id).update(
                views=F('views') + proof.reported_views,
                earnings=F('earnings') + amount,
            )
            payout, _ = MarketingPayout.objects.get_or_create(
                user=proof.share.user, campaign=proof.share.campaign,
            )
            MarketingPayout.objects.filter(pk=payout.pk).update(
                views=F('views') + proof.reported_views,
                amount=F('amount') + amount,
            )

            proof.status = MarketingProof.Status.APPROVED
            proof.credited_at = now
            proof.credited_amount = amount
            proof.save(update_fields=['status', 'credited_at', 'credited_amount'])
            proof.share.refresh_from_db()

        try:
            send_proof_congrats_email(proof)
            MarketingProof.objects.filter(pk=proof.pk).update(congrats_sent_at=now)
        except Exception:
            pass

        processed += 1
        total += amount

    return {'processed': processed, 'credits': total}


# ======================================================================
# Withdrawals (never gated by the activation fee)
# ======================================================================
@login_required
def request_withdrawal(request):
    balance = withdrawable_balance(request.user)
    in_progress_total = (
        WithdrawalRequest.objects
        .filter(
            user=request.user,
            status__in=[
                WithdrawalRequest.Status.PENDING,
                WithdrawalRequest.Status.APPROVED,
            ],
        )
        .aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    )

    initial = {}
    if request.user.addresses.exists():
        initial['phone_number'] = request.user.addresses.first().phone_number

    if request.method == 'POST':
        form = WithdrawalForm(request.POST, max_amount=balance)
        if form.is_valid():
            with transaction.atomic():
                from django.contrib.auth import get_user_model
                User = get_user_model()
                locked_user = User.objects.select_for_update().get(pk=request.user.pk)
                current_balance = withdrawable_balance(locked_user)
                amount = form.cleaned_data['amount']
                if amount > current_balance:
                    messages.error(
                        request,
                        f'Your withdrawable balance is KES {current_balance:.2f}.'
                    )
                    return render(request, 'marketing/withdraw.html', {
                        'form': form,
                        'balance': current_balance,
                        'in_progress_total': in_progress_total,
                    })

                wr = WithdrawalRequest.objects.create(
                    user=request.user,
                    phone_number=form.cleaned_data['phone_number'],
                    amount=amount,
                )

            try:
                send_withdrawal_requested_email(wr)
            except Exception:
                pass

            messages.success(
                request,
                f'Withdrawal in progress. KES {wr.amount} will be sent to '
                f'{wr.phone_number} within 24 hours. We\'ll email you the '
                f'M-Pesa receipt when it\'s paid.'
            )
            return redirect('marketing:withdrawals')
    else:
        form = WithdrawalForm(max_amount=balance, initial=initial)

    return render(request, 'marketing/withdraw.html', {
        'form': form,
        'balance': balance,
        'in_progress_total': in_progress_total,
    })


@login_required
@require_GET
def withdrawal_history(request):
    withdrawals = (
        WithdrawalRequest.objects
        .filter(user=request.user)
        .order_by('-requested_at')
    )
    return render(request, 'marketing/withdrawals.html', {
        'withdrawals': withdrawals,
        'balance': withdrawable_balance(request.user),
    })


# ======================================================================
# Cron endpoint
# ======================================================================
def process_proofs_endpoint(request):
    secret = request.headers.get('x-cron-secret') or request.GET.get('secret')
    expected = getattr(settings, 'CRON_SECRET', None)
    if not expected or secret != expected:
        return JsonResponse({'error': 'forbidden'}, status=403)

    result = process_due_proofs()
    return JsonResponse({
        'ok': True,
        'processed': result['processed'],
        'credits': str(result['credits']),
    })


import requests
from urllib.parse import quote
from django.http import HttpResponse, Http404
from django.utils.text import slugify


@require_GET
def download_campaign_media(request, campaign_id):
    """
    Proxy-download a campaign's image or video so the browser saves the
    file with a friendly name instead of navigating to Cloudinary. The
    HTML `download` attribute is ignored cross-origin, so we stream the
    file through our own domain and set Content-Disposition manually.
    """
    campaign = get_object_or_404(MarketingCampaign, id=campaign_id)

    if campaign.media_type == MarketingCampaign.MediaType.VIDEO and campaign.video:
        source_url = campaign.video.url
        ext = 'mp4'
        default_type = 'video/mp4'
    elif campaign.image:
        source_url = campaign.image.url
        from pathlib import PurePosixPath
        path_ext = PurePosixPath(source_url.split('?')[0]).suffix.lstrip('.').lower()
        ext = path_ext or 'jpg'
        default_type = 'image/jpeg'
    else:
        raise Http404('This campaign has no media to download.')

    try:
        upstream = requests.get(source_url, stream=True, timeout=30)
        upstream.raise_for_status()
    except requests.RequestException as exc:
        raise Http404(f'Could not fetch media: {exc}')

    base = slugify(campaign.title)[:60] or 'campaign'
    filename = f'{base}-{campaign.id}.{ext}'

    response = HttpResponse(
        upstream.content,
        content_type=upstream.headers.get('Content-Type', default_type),
    )
    response['Content-Disposition'] = (
        f'attachment; filename="{filename}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )
    response['Content-Length'] = len(upstream.content)
    return response