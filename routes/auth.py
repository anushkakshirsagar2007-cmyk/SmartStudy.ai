import time
import re
import hashlib
import secrets
from datetime import datetime, timedelta
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user
from models import User, PasswordResetToken
from extensions import db
from mail import send_email

auth_bp = Blueprint('auth', __name__)

_FAILS = {}
MAX_FAILS, WINDOW = 5, 300
_ACTION_REQUESTS = {}
ACTION_LIMIT, ACTION_WINDOW = 3, 300


def _action_limited(action, identity):
    now = time.time()
    key = f'{action}:{request.remote_addr}:{identity}'
    timestamps = [t for t in _ACTION_REQUESTS.get(key, []) if now - t < ACTION_WINDOW]
    _ACTION_REQUESTS[key] = timestamps
    if len(timestamps) >= ACTION_LIMIT:
        return True
    timestamps.append(now)
    return False


def _token_serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='smartstudy-email-verification')


def _verification_url(user):
    token = _token_serializer().dumps({'user_id': user.id, 'email': user.email})
    base = current_app.config['SITE_URL'] or request.url_root.rstrip('/')
    return f'{base}{url_for("auth.verify_email", token=token)}'


def _send_verification(user):
    if _action_limited('verification-email', user.email):
        return False
    link = _verification_url(user)
    send_email(user.email, 'Verify your SmartStudy.ai email',
               f'Hi {user.full_name},\n\nVerify your email address using this link (valid for 24 hours):\n{link}\n')
    return True


def _password_reset_url(token):
    base = current_app.config['SITE_URL'] or request.url_root.rstrip('/')
    return f'{base}{url_for("auth.reset_password", token=token)}'

def _login_key(email):
    return f"{request.remote_addr}:{email}"

def login_locked(email):
    now = time.time()
    _FAILS[_login_key(email)] = [t for t in _FAILS.get(_login_key(email), []) if now - t < WINDOW]
    return len(_FAILS[_login_key(email)]) >= MAX_FAILS

def validate_password(password):
    """Validate password strength."""
    errors = []
    if len(password) < 8:
        errors.append('Password must be at least 8 characters long.')
    if not re.search(r'[A-Z]', password):
        errors.append('Password must contain at least one uppercase letter.')
    if not re.search(r'[a-z]', password):
        errors.append('Password must contain at least one lowercase letter.')
    if not re.search(r'\d', password):
        errors.append('Password must contain at least one digit.')
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        errors.append('Password must contain at least one special character.')
    return errors

def validate_email(email):
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        if current_user.is_admin():
            return redirect(url_for('admin.dashboard'))
        return redirect(url_for('student.dashboard'))
    
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        username = request.form.get('username', '').strip().lower()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        consent = request.form.get('agree_terms') == 'on'
        
        errors = []
        
        if not full_name or len(full_name) < 2:
            errors.append('Full name must be at least 2 characters.')
        if not username or len(username) < 3:
            errors.append('Username must be at least 3 characters.')
        if not re.match(r'^[a-z0-9_]+$', username):
            errors.append('Username can only contain lowercase letters, numbers, and underscores.')
        if not email or not validate_email(email):
            errors.append('Please enter a valid email address.')
        if not consent:
            errors.append('You must agree to the Terms and Privacy Policy to create an account.')
        
        pw_errors = validate_password(password)
        errors.extend(pw_errors)
        
        if password != confirm_password:
            errors.append('Passwords do not match.')
        
        if not errors:
            # Check uniqueness
            if User.query.filter_by(email=email).first():
                errors.append('An account with this email already exists.')
            if User.query.filter_by(username=username).first():
                errors.append('This username is already taken.')
        
        if errors:
            for error in errors:
                flash(error, 'error')
            return render_template('auth/register.html', 
                                   form_data={'full_name': full_name, 'username': username, 'email': email})
        
        # Create user
        import random
        colors = ['#7C3AED', '#5B21B6', '#4C1D95', '#6D28D9', '#8B5CF6', '#A78BFA']
        user = User(
            full_name=full_name,
            username=username,
            email=email,
            avatar_color=random.choice(colors),
            role='student',
            consent_at=datetime.utcnow()
        )
        user.set_password(password)
        
        db.session.add(user)
        db.session.commit()

        _send_verification(user)
        flash('Account created. Please log in and check your email to verify your address.', 'success')
        return redirect(url_for('auth.login'))
    
    return render_template('auth/register.html', form_data={})


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        if current_user.is_admin():
            return redirect(url_for('admin.dashboard'))
        return redirect(url_for('student.dashboard'))
    
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        remember = request.form.get('remember') == 'on'
        
        if not email or not password:
            flash('Please enter both email and password.', 'error')
            return render_template('auth/login.html', form_data={'email': email})
        
        if login_locked(email):
            flash('Too many failed attempts. Please wait 5 minutes and try again.', 'error')
            return render_template('auth/login.html', form_data={'email': email}), 429

        user = User.query.filter_by(email=email).first()

        if not user or not user.check_password(password):
            _FAILS.setdefault(_login_key(email), []).append(time.time())
            flash('Invalid email or password. Please try again.', 'error')
            return render_template('auth/login.html', form_data={'email': email})
        
        if not user.is_active:
            flash('Your account has been deactivated. Please contact support.', 'error')
            return render_template('auth/login.html', form_data={'email': email})
        
        _FAILS.pop(_login_key(email), None)
        login_user(user, remember=remember)
        user.last_login = datetime.utcnow()
        db.session.commit()
        
        flash(f'Welcome back, {user.full_name}!', 'success')
        
        next_page = request.args.get('next')
        if next_page and next_page.startswith('/') and not next_page.startswith('//'):
            return redirect(next_page)
        
        if user.is_admin():
            return redirect(url_for('admin.dashboard'))
        return redirect(url_for('student.dashboard'))
    
    return render_template('auth/login.html', form_data={})


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        if not _action_limited('forgot-password', email or 'missing'):
            user = User.query.filter_by(email=email).first() if validate_email(email) else None
            if user and user.is_active:
                token = secrets.token_urlsafe(32)
                now = datetime.utcnow()
                PasswordResetToken.query.filter_by(user_id=user.id, used_at=None).update(
                    {'used_at': now}, synchronize_session=False)
                reset = PasswordResetToken(
                    user_id=user.id,
                    token_hash=hashlib.sha256(token.encode('utf-8')).hexdigest(),
                    expires_at=now + timedelta(minutes=30)
                )
                db.session.add(reset)
                db.session.commit()
                send_email(user.email, 'Reset your SmartStudy.ai password',
                           f'Use this single-use password reset link within 30 minutes:\n{_password_reset_url(token)}\n')
        flash('If an account matches that email, password reset instructions will be sent shortly.', 'info')
        return redirect(url_for('auth.forgot_password'))
    return render_template('auth/forgot_password.html')


@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
    reset = PasswordResetToken.query.filter_by(token_hash=token_hash, used_at=None).first()
    valid = bool(reset and reset.expires_at > datetime.utcnow())
    if not valid:
        flash('This password reset link is invalid or has expired. Request a new one.', 'error')
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        errors = validate_password(password)
        if password != confirm_password:
            errors.append('Passwords do not match.')
        if errors:
            for error in errors:
                flash(error, 'error')
            return render_template('auth/reset_password.html')

        user = db.session.get(User, reset.user_id)
        if not user:
            flash('This password reset link is invalid or has expired. Request a new one.', 'error')
            return redirect(url_for('auth.forgot_password'))
        user.set_password(password)
        reset.used_at = datetime.utcnow()
        db.session.commit()
        flash('Your password has been reset. Please sign in.', 'success')
        return redirect(url_for('auth.login'))
    return render_template('auth/reset_password.html')


@auth_bp.route('/verify-email/<token>')
def verify_email(token):
    try:
        payload = _token_serializer().loads(token, max_age=24 * 60 * 60)
    except SignatureExpired:
        flash('This verification link has expired. Sign in to request another.', 'error')
        return redirect(url_for('auth.login'))
    except BadSignature:
        flash('This verification link is invalid.', 'error')
        return redirect(url_for('auth.login'))

    user = db.session.get(User, payload.get('user_id'))
    if not user or user.email != payload.get('email'):
        flash('This verification link is invalid.', 'error')
    elif not user.is_verified:
        user.is_verified = True
        user.email_verified_at = datetime.utcnow()
        db.session.commit()
        flash('Your email address has been verified.', 'success')
    else:
        flash('Your email address is already verified.', 'info')
    return redirect(url_for('student.dashboard') if current_user.is_authenticated else url_for('auth.login'))


@auth_bp.route('/resend-verification', methods=['POST'])
@login_required
def resend_verification():
    if current_user.is_verified:
        flash('Your email address is already verified.', 'info')
    elif not _send_verification(current_user):
        flash('Please wait before requesting another verification email.', 'error')
    else:
        flash('If your account needs verification, a new link has been sent.', 'info')
    return redirect(request.referrer or url_for('student.dashboard'))


@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('main.index'))
