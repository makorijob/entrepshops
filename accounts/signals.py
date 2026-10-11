from allauth.account.models import EmailAddress
from allauth.account.signals import user_signed_up
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Profile

User = get_user_model()


@receiver(post_save, sender=User)
def create_profile_for_new_user(sender, instance, created, **kwargs):
    if created:
        Profile.objects.get_or_create(user=instance)


@receiver(user_signed_up)
def link_referral_on_signup(request, user, **kwargs):
    """Fired by allauth right after a signup completes (username/password
    or Google). Looks for a referral code captured earlier in the session
    (see accounts/middleware.py) and, if valid, links the two accounts.

    No wallet credit happens here. The two-stage referral payout is:

      Stage 1 (email verification) — see referral_email_bonus_on_verify
        below. Fires when the referred user verifies their email address.

      Stage 2 (first paid order) — see Profile.release_first_order_bonus_if_due,
        called from Order.mark_paid().

    Paying out at signup would reward fake accounts with no real behavior.
    Requiring a verified email + a genuine first order is the actual signal
    that this was a real referral.
    """
    code = request.session.pop('referral_code', None)
    if not code:
        return

    referrer_profile = Profile.objects.filter(referral_code__iexact=code).exclude(user=user).first()
    if not referrer_profile:
        return

    new_user_profile, _ = Profile.objects.get_or_create(user=user)
    if new_user_profile.referred_by_id:
        return  # already attributed somehow — don't double-link

    new_user_profile.referred_by = referrer_profile.user
    new_user_profile.save(update_fields=['referred_by'])


@receiver(post_save, sender=EmailAddress)
def referral_email_bonus_on_verify(sender, instance, created, **kwargs):
    """Fires every time an EmailAddress row is saved. We act only when
    the row is verified — then we trigger the stage-1 referral payout
    on that user's Profile.

    Idempotent — the release_email_bonus_if_due method checks its own
    flag and returns early if the bonus was already paid.
    """
    if not instance.verified:
        return

    try:
        profile = instance.user.profile
    except Profile.DoesNotExist:
        # Profile signal hasn't run yet, or user has no profile — skip.
        return

    try:
        profile.release_email_bonus_if_due()
    except Exception:
        # Never let a referral payout failure break the email-verify flow.
        import logging
        logging.getLogger(__name__).exception(
            'Failed to release referral email bonus for user=%s',
            instance.user_id,
        )