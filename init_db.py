import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from extensions import create_app, db
from models import User
from schema_migrations import migrate_legacy_columns

def init_database():
    """Initialize the database and create admin user."""
    app = create_app()
    
    with app.app_context():
        # Create all tables
        db.create_all()
        migrate_legacy_columns()
        print("✅ Database tables created.")
        
        # Create admin user
        admin_email = os.environ.get('ADMIN_EMAIL', 'admin@smartstudy.ai')
        admin_password = os.environ.get('ADMIN_PASSWORD')
        generated = False
        if not admin_password:
            import secrets
            admin_password = secrets.token_urlsafe(10) + 'aA1!'
            generated = True
        
        existing_admin = User.query.filter_by(email=admin_email).first()
        if not existing_admin:
            admin = User(
                full_name='Admin User',
                username='admin',
                email=admin_email,
                role='admin',
                avatar_color='#DC2626',
                is_active=True,
                subscription_plan='free'
            )
            admin.set_password(admin_password)
            db.session.add(admin)
            db.session.commit()
            print(f"✅ Admin user created: {admin_email}")
            print(f"   Generated password (shown once, change it after login): {admin_password}" if generated else "   Password: taken from ADMIN_PASSWORD")
        else:
            print(f"ℹ️  Admin user already exists: {admin_email}")
        
        print("\n🚀 SmartStudy.ai database initialized successfully!")
        print("\nLogin Credentials:")
        print(f"  Admin: {admin_email}")
        print("\n📌 New students register at /auth/register")
        print("📌 Add your OpenAI API key to .env for AI features")
        print("📌 Set UPI_ID in .env to accept manual UPI payments")

if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv()
    init_database()
