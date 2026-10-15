from django.urls import path

from . import views

app_name = 'store'

urlpatterns = [
    path('', views.home, name='home'),
    path('product/<slug:slug>/', views.product_detail, name='product_detail'),

    path('cart/', views.cart_detail, name='cart_detail'),
    path('cart/add/<int:product_id>/', views.cart_add, name='cart_add'),
    path('cart/remove/<int:product_id>/', views.cart_remove, name='cart_remove'),
    path('cart/update/<int:product_id>/', views.cart_update, name='cart_update'),

    path('addresses/', views.address_list, name='address_list'),
    path('addresses/add/', views.address_add, name='address_add'),

    # Vehicle rentals
path('rent/<int:product_id>/', views.rental_checkout, name='rental_checkout'),
path('rent/<int:product_id>/pay/', views.rental_start_payment, name='rental_start_payment'),
path('rent/<int:product_id>/availability/', views.rental_availability, name='rental_availability'),
path('rent/booking/<int:booking_id>/', views.rental_success, name='rental_success'),
path('rent/booking/<int:booking_id>/status/', views.rental_status, name='rental_status'),
path('my-rentals/', views.my_rentals, name='my_rentals'),
    path('products/', views.product_list, name='product_list'),
    
]
