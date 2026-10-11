from django.urls import path

from . import views

app_name = 'marketing'

urlpatterns = [
    # Campaign + sharing
    path('', views.campaign_list, name='list'),
    path('share/<int:campaign_id>/create/', views.create_share, name='create_share'),
    path('r/<str:code>/', views.redirect_view, name='redirect'),

    # Activation (paywall)
    path('subscribe/', views.subscribe, name='subscribe'),
    path('subscribe/pay/', views.start_activation_payment, name='start_activation_payment'),
    path('subscribe/status/', views.activation_status, name='activation_status'),

    # Earnings + withdrawals
    path('earnings/', views.my_earnings, name='earnings'),
    path('proof/<int:share_id>/upload/', views.upload_proof, name='upload_proof'),
    path('withdraw/', views.request_withdrawal, name='withdraw'),
    path('withdrawals/', views.withdrawal_history, name='withdrawals'),

    # Cron
    path('internal/process-proofs/', views.process_proofs_endpoint, name='process_proofs'),
    path('campaign/<int:campaign_id>/download/', views.download_campaign_media, name='download_media'),
]