from django.urls import path

from . import views

app_name = 'vendors'

urlpatterns = [
    path('', views.become_vendor, name='become_vendor'),
    path('start-signup/', views.start_signup, name='start_signup'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('shop/<int:vendor_id>/', views.public_profile, name='public_profile'),
    path('profile/', views.edit_profile, name='edit_profile'),

    # Subscription
    path('subscribe/', views.subscribe, name='subscribe'),
    path('subscribe/pay/', views.start_subscription_payment, name='start_subscription_payment'),
    path('subscribe/status/', views.subscription_status, name='subscription_status'),

    # Withdrawals
    path('withdraw/', views.withdraw, name='withdraw'),
    path('withdrawals/', views.withdrawal_history, name='withdrawal_history'),

    # Products
    path('products/', views.product_list, name='product_list'),
    path('products/new/', views.product_create, name='product_create'),
    path('products/ai-suggest/', views.ai_suggest, name='ai_suggest'),
    path('products/<int:product_id>/edit/', views.product_edit, name='product_edit'),
    path('products/<int:product_id>/delete/', views.product_delete, name='product_delete'),
]