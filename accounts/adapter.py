from allauth.account.adapter import DefaultAccountAdapter
from django.contrib.auth import get_user_model
from django import forms

User = get_user_model()


class AccountAdapter(DefaultAccountAdapter):
    """Enforce case-insensitive username uniqueness at form-validation time.

    Allauth's default username check is case-sensitive and can miss
    collisions that Postgres then rejects with an IntegrityError.
    """

    def clean_username(self, username, shallow=False):
        username = super().clean_username(username, shallow=shallow)
        if not username:
            return username

        # The super() call already checks for exact matches. Add a
        # case-insensitive check to catch "Graciek" vs "graciek".
        qs = User.objects.filter(username__iexact=username)
        if self.request and self.request.user.is_authenticated:
            qs = qs.exclude(pk=self.request.user.pk)
        if qs.exists():
            raise forms.ValidationError(
                'A user with that username already exists.'
            )
        return username