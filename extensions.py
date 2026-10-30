import os
from flask import Flask, request, jsonify, flash, redirect
from flask_wtf.csrf import CSRFProtect, CSRFError
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from dotenv import load_dotenv

load_dotenv()

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()


def _database_uri_from_env():
    database_url = os.environ.get('DATABASE_URL', '').strip()
    if not database_url:
        return 'sqlite:///smartstudy.db'
    if database_url.startswith('postgres://'):
        return database_url.replace('postgres://', 'postgresql+psycopg://', 1)
    if database_url.startswith('postgresql://'):
        return database_url.replace('postgresql://', 'postgresql+psycopg://', 1)
    return database_url


def create_app():
    app = Flask(__name__)
    
    # Configuration
    secret = os.environ.get('SECRET_KEY', '')
    if not secret:
        if os.environ.get('FLASK_DEBUG', 'False').lower() == 'true':
            secret = 'dev-only-insecure-key'
        else:
            raise RuntimeError('SECRET_KEY is not set. Add a long random value to your environment (.env).')
    app.config['SECRET_KEY'] = secret
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE'] = os.environ.get('COOKIE_SECURE') == '1'
    app.config['REMEMBER_COOKIE_HTTPONLY'] = True
    app.config['REMEMBER_COOKIE_SECURE'] = os.environ.get('COOKIE_SECURE') == '1'
    app.config['WTF_CSRF_TIME_LIMIT'] = None
    app.config['SQLALCHEMY_DATABASE_URI'] = _database_uri_from_env()
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
    # File uploads: Removed strict limit (1GB buffer) and added image formats (png, jpg, jpeg, webp)
    app.config['MAX_CONTENT_LENGTH'] = min(10 * 1024 * 1024, int(os.environ.get('MAX_CONTENT_LENGTH', 10 * 1024 * 1024)))
    app.config['ALLOWED_EXTENSIONS'] = {'pdf', 'txt', 'docx', 'md', 'png', 'jpg', 'jpeg', 'webp'}
    app.config['OPENAI_API_KEY'] = os.environ.get('OPENAI_API_KEY', '')
    app.config['OPENAI_MODEL'] = os.environ.get('OPENAI_MODEL', 'gpt-4o-mini')
    app.config['OPENAI_TIMEOUT'] = float(os.environ.get('OPENAI_TIMEOUT', '30'))
    
    app.config['UPI_ID'] = os.environ.get('UPI_ID', '').strip()
    app.config['SMTP_HOST'] = os.environ.get('SMTP_HOST', '')
    app.config['SMTP_PORT'] = int(os.environ.get('SMTP_PORT', '587'))
    app.config['SMTP_USER'] = os.environ.get('SMTP_USER', '')
    app.config['SMTP_PASSWORD'] = os.environ.get('SMTP_PASSWORD', '')
    app.config['MAIL_FROM'] = os.environ.get('MAIL_FROM') or os.environ.get('CONTACT_EMAIL') or 'no-reply@smartstudy.ai'
    app.config['SITE_URL'] = os.environ.get('SITE_URL', '').rstrip('/')
    app.config['BUSINESS_NAME'] = os.environ.get('BUSINESS_NAME', 'SmartStudy.ai')
    app.config['CONTACT_EMAIL'] = os.environ.get('CONTACT_EMAIL') or os.environ.get('SUPPORT_EMAIL') or 'support@example.com'
    app.config['REFUND_WINDOW'] = os.environ.get('REFUND_WINDOW', 'within 7 days of payment')
    app.config['SCANNED_PDF_PAGE_LIMIT'] = min(5, max(1, int(os.environ.get('SCANNED_PDF_PAGE_LIMIT', '3'))))
    
    # Initialize extensions
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'info'
    
    # Ensure upload folder exists
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    
    # Register blueprints
    from routes.auth import auth_bp
    from routes.student import student_bp
    from routes.admin import admin_bp
    from routes.api import api_bp
    from routes.main import main_bp
    from routes.payment import payment_bp
    
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(student_bp, url_prefix='/student')
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(payment_bp, url_prefix='/payment')
    
    @app.errorhandler(CSRFError)
    def csrf_error(e):
        if request.is_json or request.headers.get('X-CSRFToken'):
            return jsonify({'error': 'Your session expired. Refresh the page and try again.'}), 400
        flash('Your session expired. Please try again.', 'error')
        return redirect(request.referrer or '/')

    @app.errorhandler(413)
    def too_large(e):
        limit = app.config['MAX_CONTENT_LENGTH'] // (1024 * 1024)
        if request.is_json:
            return jsonify({'error': f'File too large (max {limit} MB).'}), 413
        flash(f'File too large (max {limit} MB).', 'error')
        return redirect(request.referrer or '/')

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
        resp.headers.setdefault('X-Frame-Options', 'DENY')
        resp.headers.setdefault('Referrer-Policy', 'same-origin')
        return resp

    @app.context_processor
    def inject_globals():
        from datetime import datetime
        from models import PLAN_DETAILS, FREE_UPLOAD_LIMIT
        return {
            'now': datetime.utcnow(),
            'PLAN_DETAILS': PLAN_DETAILS,
            'FREE_UPLOAD_LIMIT': FREE_UPLOAD_LIMIT,
            'UPI_ID': app.config['UPI_ID'],
            'business_name': app.config['BUSINESS_NAME'],
            'contact_email': app.config['CONTACT_EMAIL'],
            'refund_window': app.config['REFUND_WINDOW']
        }

    return app
