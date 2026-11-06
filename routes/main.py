import os
from flask import Blueprint, current_app, render_template, request, Response, send_from_directory

main_bp = Blueprint('main', __name__)

def _site_url():
    return (os.environ.get('SITE_URL') or request.url_root).rstrip('/')

@main_bp.app_context_processor
def inject_site():
    return {'site_url': _site_url(),
            'support_email': current_app.config['CONTACT_EMAIL']}

@main_bp.route('/')
def index():
    return render_template('index.html')

@main_bp.route('/pricing')
def pricing():
    """Public pricing page (no login required)."""
    return render_template('pricing.html')

@main_bp.route('/terms')
def terms():
    return render_template('legal.html', page='terms')

@main_bp.route('/privacy')
def privacy():
    return render_template('legal.html', page='privacy')

@main_bp.route('/refund')
def refund():
    return render_template('legal.html', page='refund')

@main_bp.route('/favicon.ico')
def favicon():
    return send_from_directory(
        os.path.join(current_app.static_folder, 'images'),
        'favicon.ico',
        mimetype='image/vnd.microsoft.icon'
    )

@main_bp.route('/googlee6eb1f6d096a6024.html')
def google_site_verification():
    return send_from_directory(
        os.path.join(current_app.root_path, 'public'),
        'googlee6eb1f6d096a6024.html',
        mimetype='text/html'
    )

@main_bp.route('/robots.txt')
def robots():
    body = ("User-agent: *\nAllow: /\n"
            "Disallow: /student/\nDisallow: /admin/\nDisallow: /api/\nDisallow: /payment/\nDisallow: /auth/login\n"
            f"Sitemap: {_site_url()}/sitemap.xml\n")
    return Response(body, mimetype='text/plain')

@main_bp.route('/sitemap.xml')
def sitemap():
    base = _site_url()
    paths = ['/', '/pricing', '/auth/register', '/terms', '/privacy', '/refund']
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    xml += ''.join(f'  <url><loc>{base}{x}</loc></url>\n' for x in paths) + '</urlset>\n'
    return Response(xml, mimetype='application/xml')
