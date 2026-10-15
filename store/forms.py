from datetime import date, timedelta

from django import forms
from django.core.exceptions import ValidationError

from .models import VehicleRental


class VehicleRentalForm(forms.Form):
    """The booking form on the rental checkout page."""

    pickup_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}),
        help_text='When do you want the vehicle?',
    )
    return_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}),
        help_text='When will you bring it back?',
    )
    delivery_full_name = forms.CharField(
        max_length=150,
        label='Full name',
        widget=forms.TextInput(attrs={'placeholder': 'As it appears on your ID'}),
    )
    delivery_phone = forms.CharField(
        max_length=20,
        label='Phone number',
        widget=forms.TextInput(attrs={'placeholder': '2547XXXXXXXX'}),
    )
    delivery_address = forms.CharField(
        widget=forms.Textarea(attrs={
            'rows': 3,
            'placeholder': 'Where should the vehicle be delivered? Include '
                           'building, street, area, and any landmarks.',
        }),
        label='Delivery address',
    )
    delivery_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'rows': 2,
            'placeholder': 'Gate code, preferred drop-off time, etc. (optional)',
        }),
        label='Delivery notes',
    )
    driver_license = forms.ImageField(
        label='Driving license photo',
        help_text='A clear photo of your valid driving license. We verify it before pickup.',
    )
    customer_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'rows': 2,
            'placeholder': 'Anything else we should know? (optional)',
        }),
        label='Notes',
    )

    def __init__(self, *args, product=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.product = product
        self.user = user

        today = date.today()
        self.fields['pickup_date'].initial = today
        self.fields['return_date'].initial = today + timedelta(days=1)

        self.fields['pickup_date'].widget.attrs['min'] = today.isoformat()
        self.fields['return_date'].widget.attrs['min'] = (today + timedelta(days=1)).isoformat()

    def clean_pickup_date(self):
        d = self.cleaned_data['pickup_date']
        if d < date.today():
            raise ValidationError('Pickup date cannot be in the past.')
        return d

    def clean(self):
        cleaned = super().clean()
        pickup = cleaned.get('pickup_date')
        ret = cleaned.get('return_date')

        if pickup and ret:
            if ret <= pickup:
                self.add_error('return_date', 'Return date must be after the pickup date.')
                return cleaned

            total_days = (ret - pickup).days

            if self.product:
                min_days = self.product.minimum_days or 1
                if total_days < min_days:
                    self.add_error(
                        'return_date',
                        f'This vehicle has a minimum rental of {min_days} '
                        f'day{"s" if min_days != 1 else ""}.',
                    )
                    return cleaned

                if VehicleRental.has_overlap(self.product, pickup, ret):
                    raise ValidationError(
                        'This vehicle is already booked for part of that '
                        'window. Please pick different dates.'
                    )

        return cleaned

    @property
    def total_days(self):
        if not self.is_valid():
            return 0
        return (self.cleaned_data['return_date'] - self.cleaned_data['pickup_date']).days

    @property
    def subtotal(self):
        if not self.product or not self.total_days:
            return 0
        return self.product.daily_rate * self.total_days

    @property
    def total_amount(self):
        if not self.product:
            return 0
        deposit = self.product.rental_deposit or 0
        return self.subtotal + deposit
