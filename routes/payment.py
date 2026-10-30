import re
import secrets
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, current_app, render_template
from flask_login import login_required, current_user
from models import (
    Subscription, User, SubscriptionActivation,
    PLAN_MONTHLY, PLAN_ANNUAL, PLAN_INSTITUTION, PLAN_DETAILS
)
from extensions import db
from mail import send_email

payment_bp = Blueprint('payment', __name__)


@payment_bp.route('/create-order', methods=['POST'])
@login_required
def create_order():
    """Create a pending manual-UPI subscription order."""
    data = request.get_json(silent=True) or {}
    plan_type = data.get('plan_type', '')
    if plan_type not in (PLAN_MONTHLY, PLAN_ANNUAL, PLAN_INSTITUTION):
        return jsonify({'error': 'Invalid plan type'}), 400

    upi_id = current_app.config.get('UPI_ID', '')
    if not upi_id:
        return jsonify({'error': 'UPI payments are not configured. Set UPI_ID on the server.'}), 503

    plan = PLAN_DETAILS[plan_type]
    order_id = f'ss_upi_{current_user.id}_{secrets.token_urlsafe(12)}'
    subscription = Subscription(
        user_id=current_user.id,
        plan_type=plan_type,
        amount_paise=plan['price'],
        order_id=order_id,
        payment_status='pending'
    )
    db.session.add(subscription)
    db.session.commit()

    return jsonify({
        'success': True,
        'gateway': 'upi_manual',
        'order_id': order_id,
        'amount': plan['price'],
        'amount_inr': plan['price'] // 100,
        'currency': 'INR',
        'upi_id': upi_id,
        'plan_type': plan_type,
        'plan_name': plan['name'],
        'user_name': current_user.full_name,
        'subscription_id': subscription.id
    })


def activate_subscription(sub, user):
    """Activate a manually approved payment once; caller must commit."""
    if sub.payment_status == 'paid' and sub.is_active:
        return sub.expires_at
    db.session.add(SubscriptionActivation(
        subscription_id=sub.id,
        payment_id=sub.payment_reference or sub.order_id
    ))
    now = datetime.utcnow()
    days = 365 if sub.plan_type == PLAN_ANNUAL else 30
    for previous in Subscription.query.filter_by(user_id=user.id, is_active=True).all():
        previous.is_active = False
    sub.payment_status = 'paid'
    sub.is_active = True
    sub.starts_at = now
    sub.expires_at = now + timedelta(days=days)
    user.subscription_plan = sub.plan_type
    return sub.expires_at


def send_payment_receipt(sub, user):
    plan_name = PLAN_DETAILS[sub.plan_type]['name']
    amount = f'{sub.amount_paise / 100:.2f} {sub.currency}'
    send_email(
        user.email,
        f'Payment receipt from {current_app.config["BUSINESS_NAME"]}',
        f'Hello {user.full_name},\n\n'
        f'Your UPI payment of {amount} for {plan_name} has been approved.\n'
        f'UTR: {sub.payment_reference or "not recorded"}\n'
        f'Plan expires: {sub.expires_at.isoformat() if sub.expires_at else "not specified"}\n\n'
        f'For questions, contact {current_app.config["CONTACT_EMAIL"]}.\n'
    )


@payment_bp.route('/direct-upi-verify', methods=['POST'])
@login_required
def direct_upi_verify():
    """Record a student's UTR without activating their plan."""
    data = request.get_json(silent=True) or {}
    order_id = str(data.get('order_id', ''))
    utr = str(data.get('utr_number', '')).strip()

    if not re.fullmatch(r'\d{12}', utr):
        return jsonify({'error': 'Enter the 12-digit UTR / reference number shown in your UPI app.'}), 400

    subscription = Subscription.query.filter_by(
        order_id=order_id, user_id=current_user.id
    ).first()
    if not subscription or not order_id.startswith(f'ss_upi_{current_user.id}_'):
        return jsonify({'error': 'Subscription order not found'}), 404
    if subscription.payment_status not in ('pending', 'submitted'):
        return jsonify({'error': 'This order has already been processed.'}), 400
    if Subscription.query.filter(
        Subscription.payment_reference == utr,
        Subscription.id != subscription.id
    ).first():
        return jsonify({'error': 'This UTR number has already been used.'}), 400

    subscription.payment_reference = utr
    subscription.payment_status = 'submitted'
    db.session.commit()
    return jsonify({
        'success': True,
        'message': 'Payment submitted. Your plan will be activated as soon as an admin confirms the payment.'
    })


@payment_bp.route('/status')
@login_required
def subscription_status():
    """Get current subscription status."""
    active_sub = current_user.active_subscription
    return jsonify({
        'plan': current_user.subscription_plan,
        'is_pro': current_user.is_pro,
        'can_upload': current_user.can_upload,
        'uploads_remaining': current_user.uploads_remaining,
        'free_uploads_used': current_user.free_uploads_used,
        'subscription': active_sub.to_dict() if active_sub else None
    })


@payment_bp.route('/pricing')
@login_required
def pricing():
    """Show pricing plans page."""
    return render_template('student/pricing.html')
