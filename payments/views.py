import json
from datetime import timedelta

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from orders.models import Order, RiderTip

from .mpesa import MpesaError, stk_query
from .models import MpesaTransaction


@csrf_exempt
@require_POST
def mpesa_callback(request):
    """Safaricom posts the result of an STK push here.

    Handles order payments (MpesaTransaction), rider tips (RiderTip),
    vendor subscriptions (VendorSubscription), marketer activations
    (MarketerSubscription), and vehicle rentals (VehicleRental) — all
    share this one callback URL, so we look the checkout_request_id up
    in whichever table matches. Public endpoint, CSRF exempt.
    """
    try:
        data = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({'ResultCode': 1, 'ResultDesc': 'Invalid payload'})

    stk_callback = data.get('Body', {}).get('stkCallback', {})
    checkout_request_id = stk_callback.get('CheckoutRequestID')
    result_code = stk_callback.get('ResultCode')
    result_desc = stk_callback.get('ResultDesc', '')

    # 1. Order payment?
    txn = MpesaTransaction.objects.filter(checkout_request_id=checkout_request_id).first()
    if txn:
        return _handle_order_payment_callback(txn, result_code, result_desc, stk_callback)

    # 2. Rider tip?
    tip = RiderTip.objects.filter(checkout_request_id=checkout_request_id).first()
    if tip:
        return _handle_tip_callback(tip, result_code)

    # 3. Vendor subscription?
    from vendors.models import VendorSubscription
    vendor_sub = VendorSubscription.objects.filter(
        mpesa_checkout_request_id=checkout_request_id,
        status=VendorSubscription.Status.PENDING,
    ).first()
    if vendor_sub:
        return _handle_vendor_subscription_callback(
            vendor_sub, result_code, result_desc, stk_callback,
        )

    # 4. Marketer activation?
    from marketing.models import MarketerSubscription
    marketer_sub = MarketerSubscription.objects.filter(
        mpesa_checkout_request_id=checkout_request_id,
        status=MarketerSubscription.Status.PENDING,
    ).first()
    if marketer_sub:
        return _handle_marketer_subscription_callback(
            marketer_sub, result_code, result_desc, stk_callback,
        )

    # 5. Vehicle rental booking?
    from store.models import VehicleRental
    rental = VehicleRental.objects.filter(
        mpesa_checkout_request_id=checkout_request_id,
        status=VehicleRental.Status.PENDING,
    ).first()
    if rental:
        return _handle_rental_callback(rental, result_code, result_desc, stk_callback)

    return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted (no matching txn)'})


def _handle_rental_callback(rental, result_code, result_desc, stk_callback):
    """Fires when Safaricom reports the outcome of a rental booking's
    STK push. On success, moves the booking from PENDING to CONFIRMED."""
    if str(result_code) == '0':
        items = stk_callback.get('CallbackMetadata', {}).get('Item', [])
        metadata = {item.get('Name'): item.get('Value') for item in items}
        rental.mark_paid(
            receipt=str(metadata.get('MpesaReceiptNumber', '')),
            result_code=str(result_code),
            result_desc=str(result_desc)[:255],
        )
    else:
        rental.mark_failed(
            code=str(result_code),
            desc=str(result_desc)[:255],
        )
    return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})


def _handle_vendor_subscription_callback(subscription, result_code, result_desc, stk_callback):
    """Fires when Safaricom reports the outcome of the vendor subscription
    STK push. On success, activates the vendor's subscription period."""
    if str(result_code) == '0':
        items = stk_callback.get('CallbackMetadata', {}).get('Item', [])
        metadata = {item.get('Name'): item.get('Value') for item in items}
        subscription.mark_paid(
            receipt=str(metadata.get('MpesaReceiptNumber', '')),
            result_code=str(result_code),
            result_desc=str(result_desc)[:255],
        )
        try:
            from vendors.views import send_welcome_email
            send_welcome_email(subscription.vendor)
        except Exception:
            pass
    else:
        subscription.mark_failed(
            code=str(result_code),
            desc=str(result_desc)[:255],
        )
    return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})


def _handle_marketer_subscription_callback(sub, result_code, result_desc, stk_callback):
    """Fires when Safaricom reports the outcome of the marketer activation
    STK push. On success, activates the marketer's earning period."""
    if str(result_code) == '0':
        items = stk_callback.get('CallbackMetadata', {}).get('Item', [])
        metadata = {item.get('Name'): item.get('Value') for item in items}
        sub.mark_paid(
            receipt=str(metadata.get('MpesaReceiptNumber', '')),
            result_code=str(result_code),
            result_desc=str(result_desc)[:255],
        )
    else:
        sub.mark_failed(
            code=str(result_code),
            desc=str(result_desc)[:255],
        )
    return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})


def _customer_facing_reason(result_code, result_desc):
    """Translate an M-Pesa result code into something a customer can
    actually understand, instead of a raw Safaricom error string."""
    mapping = {
        1032: 'You cancelled the M-Pesa payment prompt.',
        1: 'Payment failed — insufficient M-Pesa balance.',
        1037: 'You didn\'t respond to the M-Pesa prompt in time.',
        1025: 'M-Pesa couldn\'t reach your line — please try again.',
        2001: 'The M-Pesa PIN entered was incorrect.',
    }
    try:
        code_int = int(result_code)
    except (TypeError, ValueError):
        code_int = None
    return mapping.get(code_int, result_desc or 'Payment could not be completed.')


def _handle_order_payment_callback(txn, result_code, result_desc, stk_callback):
    txn.result_description = result_desc

    if result_code == 0:
        items = stk_callback.get('CallbackMetadata', {}).get('Item', [])
        metadata = {item.get('Name'): item.get('Value') for item in items}
        txn.mpesa_receipt_number = metadata.get('MpesaReceiptNumber', '')
        txn.status = MpesaTransaction.Status.SUCCESS
        txn.save()

        order = txn.order
        order.mark_paid()
    else:
        txn.status = (
            MpesaTransaction.Status.CANCELLED if result_code == 1032
            else MpesaTransaction.Status.FAILED
        )
        txn.save()
        txn.order.mark_cancelled(_customer_facing_reason(result_code, result_desc))

    return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})


def _handle_tip_callback(tip, result_code):
    if result_code == 0:
        tip.status = RiderTip.Status.SUCCESS
    else:
        tip.status = (
            RiderTip.Status.CANCELLED if result_code == 1032
            else RiderTip.Status.FAILED
        )
    tip.save(update_fields=['status'])
    return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})


def _reconcile_stuck_transaction(txn):
    """Safety net for a lost/delayed M-Pesa callback: if a payment has sat
    in 'initiated' for more than 15 seconds, actively ask Safaricom what
    actually happened rather than leaving the customer staring at a
    spinner forever."""
    if txn.status != MpesaTransaction.Status.INITIATED:
        return
    if not txn.checkout_request_id:
        return
    if timezone.now() - txn.created_at < timedelta(seconds=15):
        return

    try:
        result = stk_query(txn.checkout_request_id)
    except MpesaError:
        return

    result_code = str(result.get('ResultCode', ''))
    if result_code == '0':
        txn.status = MpesaTransaction.Status.SUCCESS
        txn.result_description = result.get('ResultDesc', '')
        txn.save(update_fields=['status', 'result_description'])
        txn.order.mark_paid()
    elif result_code == '1032':
        txn.status = MpesaTransaction.Status.CANCELLED
        txn.result_description = result.get('ResultDesc', '')
        txn.save(update_fields=['status', 'result_description'])
        txn.order.mark_cancelled(_customer_facing_reason(1032, result.get('ResultDesc', '')))
    elif result_code:
        txn.status = MpesaTransaction.Status.FAILED
        txn.result_description = result.get('ResultDesc', '')
        txn.save(update_fields=['status', 'result_description'])
        txn.order.mark_cancelled(_customer_facing_reason(result_code, result.get('ResultDesc', '')))


def _reconcile_stuck_tip(tip):
    if tip.status != RiderTip.Status.INITIATED:
        return
    if not tip.checkout_request_id:
        return
    if timezone.now() - tip.created_at < timedelta(seconds=15):
        return

    try:
        result = stk_query(tip.checkout_request_id)
    except MpesaError:
        return

    result_code = str(result.get('ResultCode', ''))
    if result_code == '0':
        tip.status = RiderTip.Status.SUCCESS
    elif result_code == '1032':
        tip.status = RiderTip.Status.CANCELLED
    elif result_code:
        tip.status = RiderTip.Status.FAILED
    tip.save(update_fields=['status'])


@require_GET
def payment_status(request, order_id):
    """Polled by the checkout-waiting page's JS to see if payment landed."""
    order = Order.objects.filter(id=order_id, user=request.user).first()
    if not order:
        return JsonResponse({'status': 'unknown'}, status=404)

    latest_txn = order.mpesa_transactions.order_by('-created_at').first()
    if latest_txn:
        _reconcile_stuck_transaction(latest_txn)
        latest_txn.refresh_from_db()
    order.refresh_from_db()
    return JsonResponse({
        'order_status': order.status,
        'txn_status': latest_txn.status if latest_txn else None,
        'txn_message': order.cancellation_reason or (latest_txn.result_description if latest_txn else ''),
    })


@require_GET
def tip_status(request, tip_id):
    """Polled by the order page's JS to see if a rider tip landed."""
    tip = RiderTip.objects.filter(id=tip_id, order__user=request.user).first()
    if not tip:
        return JsonResponse({'status': 'unknown'}, status=404)
    _reconcile_stuck_tip(tip)
    tip.refresh_from_db()
    return JsonResponse({'tip_status': tip.status})


@csrf_exempt
@require_POST
def b2c_result(request):
    """Safaricom posts the outcome of a wallet withdrawal here once B2C is
    approved and configured. Exempt from CSRF since Safaricom, not a
    browser, calls it — same pattern as the STK callback."""
    from accounts.models import WithdrawalRequest

    try:
        data = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({'ResultCode': 1, 'ResultDesc': 'Invalid payload'})

    result = data.get('Result', {})
    conversation_id = result.get('ConversationID')
    result_code = result.get('ResultCode')
    result_desc = result.get('ResultDesc', '')

    withdrawal = WithdrawalRequest.objects.filter(mpesa_conversation_id=conversation_id).first()
    if not withdrawal:
        return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted (no matching withdrawal)'})

    if result_code == 0:
        params = {
            p.get('Key'): p.get('Value')
            for p in result.get('ResultParameters', {}).get('ResultParameter', [])
        }
        withdrawal.mark_success(receipt_number=str(params.get('TransactionReceipt', '')))
    else:
        withdrawal.mark_failed(result_desc)

    return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})


@csrf_exempt
@require_POST
def b2c_timeout(request):
    """Safaricom posts here if the B2C request itself times out in their
    queue. Treat it as a failure so the customer's wallet gets refunded
    rather than left in limbo indefinitely."""
    from accounts.models import WithdrawalRequest

    try:
        data = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({'ResultCode': 1, 'ResultDesc': 'Invalid payload'})

    conversation_id = data.get('Result', {}).get('ConversationID')
    withdrawal = WithdrawalRequest.objects.filter(mpesa_conversation_id=conversation_id).first()
    if withdrawal and withdrawal.status == withdrawal.Status.PROCESSING:
        withdrawal.mark_failed('Timed out waiting for Safaricom to process the payout.')

    return JsonResponse({'ResultCode': 0, 'ResultDesc': 'Accepted'})
