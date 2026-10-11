import json
import random

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from store.models import DeliveryAddress

from .models import DailySpin, Profile, SpinPrize, WalletTransaction, WithdrawalRequest


@login_required
def profile_view(request):
    from django.conf import settings as dj_settings

    profile, _ = Profile.objects.get_or_create(user=request.user)
    referral_link = request.build_absolute_uri(f'/?ref={profile.referral_code}')
    recent_transactions = request.user.wallet_transactions.all()[:15]
    today_spin = DailySpin.objects.filter(user=request.user, spin_date=timezone.localdate()).first()
    recent_withdrawals = request.user.withdrawals.all()[:10]
    return render(request, 'accounts/profile.html', {
        'profile': profile,
        'referral_link': referral_link,
        'recent_transactions': recent_transactions,
        'today_spin': today_spin,
        'recent_withdrawals': recent_withdrawals,
        'min_withdrawal': dj_settings.WALLET_MIN_WITHDRAWAL_KES,
        'referral_bonus': dj_settings.REFERRAL_BONUS_KES,
    })


@login_required
@require_POST
def update_profile(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)

    new_username = request.POST.get('username', '').strip()
    if new_username and new_username != request.user.username:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        if User.objects.exclude(pk=request.user.pk).filter(username__iexact=new_username).exists():
            messages.error(request, 'That username is already taken.')
            return redirect('accounts:profile')
        request.user.username = new_username
        request.user.save(update_fields=['username'])

    if request.FILES.get('avatar'):
        profile.avatar = request.FILES['avatar']
        profile.save(update_fields=['avatar'])

    messages.success(request, 'Profile updated.')
    return redirect('accounts:profile')


@login_required
def spin_page(request):
    from .models import SpinWheelTheme

    profile, _ = Profile.objects.get_or_create(user=request.user)
    today_spin = DailySpin.objects.filter(user=request.user, spin_date=timezone.localdate()).first()
    prizes = SpinPrize.objects.filter(is_active=True)
    prizes_json = [
        {
            'id': p.id,
            'label': p.label,
            'weight': p.weight,
            'image': p.image.url if p.image else None,
            'color': p.color,
        }
        for p in prizes
    ]
    wheel_theme = SpinWheelTheme.get_active()
    return render(request, 'accounts/spin.html', {
        'profile': profile,
        'today_spin': today_spin,
        'prizes': prizes,
        'prizes_json': prizes_json,
        'today_spin_label': today_spin.prize.label if today_spin else None,
        'addresses': request.user.addresses.all(),
        'wheel_theme': wheel_theme,
    })


@login_required
@require_POST
def spin_wheel(request):
    today = timezone.localdate()
    if DailySpin.objects.filter(user=request.user, spin_date=today).exists():
        messages.info(request, "You've already spun today — come back tomorrow!")
        return redirect('accounts:spin')

    prizes = list(SpinPrize.objects.filter(is_active=True))
    if not prizes:
        messages.error(request, 'The spin wheel is not available right now — check back soon.')
        return redirect('accounts:spin')

    weights = [p.weight for p in prizes]
    prize = random.choices(prizes, weights=weights, k=1)[0]

    if prize.kind == SpinPrize.Kind.TRY_AGAIN:
        claim_status = DailySpin.ClaimStatus.NOTHING_TO_CLAIM
    else:
        claim_status = DailySpin.ClaimStatus.PENDING_CLAIM

    spin = DailySpin.objects.create(user=request.user, prize=prize, spin_date=today, claim_status=claim_status)

    if prize.cash_value:
        messages.success(request, f'You won {prize.label} — claim it below to add KES {prize.cash_value} to your wallet.')
    elif prize.kind == SpinPrize.Kind.TRY_AGAIN:
        messages.info(request, 'No luck today — try again tomorrow!')
    else:
        messages.success(request, f'You won {prize.label}! Claim it below to arrange delivery.')

    return redirect('accounts:spin')


@login_required
@require_POST
def claim_prize(request, spin_id):
    spin = get_object_or_404(DailySpin, id=spin_id, user=request.user)
    if spin.claim_status != DailySpin.ClaimStatus.PENDING_CLAIM:
        messages.error(request, 'This prize has already been claimed or isn\'t claimable.')
        return redirect('accounts:spin')

    if spin.prize.cash_value:
        profile, _ = Profile.objects.get_or_create(user=request.user)
        profile.credit_wallet(
            spin.prize.cash_value, WalletTransaction.Kind.SPIN_WIN,
            description=f'Daily spin: {spin.prize.label}',
        )
        spin.claim_status = DailySpin.ClaimStatus.FULFILLED
        spin.claimed_at = timezone.now()
        spin.save(update_fields=['claim_status', 'claimed_at'])
        messages.success(request, f'Claimed! KES {spin.prize.cash_value} added to your wallet.')
        return redirect('accounts:spin')

    address_id = request.POST.get('address_id')
    address = get_object_or_404(DeliveryAddress, id=address_id, user=request.user)
    spin.claim(address)
    messages.success(request, f'Claimed! We\'ll deliver "{spin.prize.label}" to {address.label} soon.')
    return redirect('accounts:spin')


@login_required
@require_POST
def withdraw_wallet(request):
    from django.conf import settings as dj_settings

    from payments.mpesa import MpesaError, b2c_payment

    profile, _ = Profile.objects.get_or_create(user=request.user)
    min_amount = dj_settings.WALLET_MIN_WITHDRAWAL_KES

    try:
        amount = int(request.POST.get('amount', 0))
    except ValueError:
        amount = 0

    if amount < min_amount:
        messages.error(request, f'The minimum withdrawal is KES {min_amount}.')
        return redirect('accounts:profile')

    phone_number = request.POST.get('phone_number', '').strip()
    if not phone_number:
        messages.error(request, 'Please provide the M-Pesa number to withdraw to.')
        return redirect('accounts:profile')

    debited = profile.debit_wallet(
        amount, WalletTransaction.Kind.WITHDRAWAL,
        description=f'Withdrawal request to {phone_number}',
    )
    if not debited:
        messages.error(request, "That's more than your current wallet balance.")
        return redirect('accounts:profile')

    withdrawal = WithdrawalRequest.objects.create(
        user=request.user, amount=amount, phone_number=phone_number,
    )

    try:
        response = b2c_payment(
            phone_number=phone_number, amount=amount,
            remarks=f'DrinkShop wallet withdrawal #{withdrawal.id}',
        )
        withdrawal.status = WithdrawalRequest.Status.PROCESSING
        withdrawal.mpesa_conversation_id = response.get('ConversationID', '')
        withdrawal.save(update_fields=['status', 'mpesa_conversation_id'])
        messages.success(request, 'Withdrawal requested — funds should land on your phone shortly.')
    except MpesaError as exc:
        withdrawal.mark_failed(str(exc))
        messages.error(
            request,
            f'Could not process the withdrawal right now, so it has been refunded to your wallet. ({exc})'
        )

    return redirect('accounts:profile')
