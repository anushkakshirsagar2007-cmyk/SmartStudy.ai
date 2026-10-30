from datetime import datetime, timedelta
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db, login_manager


# ─── Subscription Plans ────────────────────────────────────────────────────────
PLAN_FREE = 'free'
PLAN_MONTHLY = 'monthly'
PLAN_ANNUAL = 'annual'
PLAN_INSTITUTION = 'institution'

FREE_UPLOAD_LIMIT = 3  # Max PDFs for free users

PLAN_DETAILS = {
    PLAN_FREE: {'name': 'Free', 'price': 0, 'period': None, 'upload_limit': FREE_UPLOAD_LIMIT},
    PLAN_MONTHLY: {'name': 'Pro Monthly', 'price': 4900, 'period': 'monthly', 'upload_limit': None},
    PLAN_ANNUAL: {'name': 'Pro Annual', 'price': 49900, 'period': 'yearly', 'upload_limit': None},
    PLAN_INSTITUTION: {'name': 'Institution', 'price': 200000, 'period': 'monthly', 'upload_limit': None},
}


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    full_name = db.Column(db.String(150), nullable=False)
    avatar_color = db.Column(db.String(7), default='#7C3AED')
    bio = db.Column(db.Text, default='')
    role = db.Column(db.String(20), default='student')  # 'student' or 'admin'
    is_active = db.Column(db.Boolean, default=True)
    is_verified = db.Column(db.Boolean, default=False)
    email_verified_at = db.Column(db.DateTime, nullable=True)
    consent_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    study_goal_hours = db.Column(db.Float, default=2.0)
    preferred_subject = db.Column(db.String(100), default='')
    
    # Subscription fields
    subscription_plan = db.Column(db.String(20), default='free')  # free, monthly, annual, institution
    free_uploads_used = db.Column(db.Integer, default=0)
    institution_id = db.Column(db.Integer, db.ForeignKey('institutions.id'), nullable=True)
    
    # Relationships
    notes = db.relationship('Note', backref='author', lazy='dynamic', cascade='all, delete-orphan')
    tasks = db.relationship('Task', backref='owner', lazy='dynamic', cascade='all, delete-orphan')
    quizzes = db.relationship('Quiz', backref='creator', lazy='dynamic', cascade='all, delete-orphan')
    flashcard_sets = db.relationship('FlashcardSet', backref='creator', lazy='dynamic', cascade='all, delete-orphan')
    documents = db.relationship('Document', backref='uploader', lazy='dynamic', cascade='all, delete-orphan')
    quiz_attempts = db.relationship('QuizAttempt', backref='student', lazy='dynamic', cascade='all, delete-orphan')
    chat_messages = db.relationship('ChatMessage', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    study_sessions = db.relationship('StudySession', backref='student', lazy='dynamic', cascade='all, delete-orphan')
    subscriptions = db.relationship('Subscription', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    password_reset_tokens = db.relationship('PasswordResetToken', back_populates='user', cascade='all, delete-orphan')
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def is_admin(self):
        return self.role == 'admin'
    
    @property
    def initials(self):
        parts = self.full_name.split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[-1][0]).upper()
        return self.full_name[:2].upper()
    
    # ─── Subscription helpers ──────────────────────────────────────────────────
    
    @property
    def is_pro(self):
        """Check if user has an active paid subscription."""
        if self.subscription_plan in (PLAN_MONTHLY, PLAN_ANNUAL, PLAN_INSTITUTION):
            active_sub = self.subscriptions.filter_by(is_active=True).first()
            if active_sub and (active_sub.expires_at is None or active_sub.expires_at > datetime.utcnow()):
                return True
            # If subscription expired, downgrade
            if active_sub and active_sub.expires_at and active_sub.expires_at <= datetime.utcnow():
                active_sub.is_active = False
                self.subscription_plan = PLAN_FREE
                db.session.commit()
        return False
    
    @property
    def can_upload(self):
        """Check if user can upload more documents."""
        if self.is_pro:
            return True
        return self.free_uploads_used < FREE_UPLOAD_LIMIT
    
    @property
    def uploads_remaining(self):
        """Get number of free uploads remaining."""
        if self.is_pro:
            return -1  # Unlimited
        return max(0, FREE_UPLOAD_LIMIT - self.free_uploads_used)
    
    @property
    def active_subscription(self):
        """Get current active subscription."""
        return self.subscriptions.filter_by(is_active=True).first()
    
    @property
    def plan_display_name(self):
        """Display-friendly plan name."""
        details = PLAN_DETAILS.get(self.subscription_plan)
        if details:
            return details['name']
        return 'Free'
    
    def __repr__(self):
        return f'<User {self.email}>'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


class Institution(db.Model):
    __tablename__ = 'institutions'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(20), default='')
    address = db.Column(db.Text, default='')
    max_students = db.Column(db.Integer, default=100)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    students = db.relationship('User', backref='institution', lazy='dynamic')
    subscriptions = db.relationship('Subscription', backref='institution', lazy='dynamic')
    
    def __repr__(self):
        return f'<Institution {self.name}>'


class Subscription(db.Model):
    __tablename__ = 'subscriptions'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    institution_id = db.Column(db.Integer, db.ForeignKey('institutions.id'), nullable=True)
    plan_type = db.Column(db.String(20), nullable=False)  # monthly, annual, institution
    amount_paise = db.Column(db.Integer, nullable=False)  # Amount in paise
    currency = db.Column(db.String(3), default='INR')
    
    # Payment info
    order_id = db.Column('razorpay_order_id', db.String(100), nullable=True, index=True)
    payment_reference = db.Column('razorpay_payment_id', db.String(100), nullable=True)
    payment_status = db.Column(db.String(20), default='pending')  # pending, paid, failed, refunded
    
    # Subscription period
    is_active = db.Column(db.Boolean, default=False)
    starts_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    activations = db.relationship('SubscriptionActivation', back_populates='subscription', cascade='all, delete-orphan')
    
    def to_dict(self):
        return {
            'id': self.id,
            'plan_type': self.plan_type,
            'amount': self.amount_paise / 100,
            'payment_status': self.payment_status,
            'is_active': self.is_active,
            'starts_at': self.starts_at.isoformat() if self.starts_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'created_at': self.created_at.isoformat()
        }


class PasswordResetToken(db.Model):
    __tablename__ = 'password_reset_tokens'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    user = db.relationship('User', back_populates='password_reset_tokens')


class SubscriptionActivation(db.Model):
    __tablename__ = 'subscription_activations'

    id = db.Column(db.Integer, primary_key=True)
    subscription_id = db.Column(db.Integer, db.ForeignKey('subscriptions.id'), nullable=False, unique=True)
    payment_id = db.Column(db.String(100), nullable=False, unique=True)
    activated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    subscription = db.relationship('Subscription', back_populates='activations')


class Note(db.Model):
    __tablename__ = 'notes'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, default='')
    subject = db.Column(db.String(100), default='General')
    tags = db.Column(db.String(500), default='')
    is_bookmarked = db.Column(db.Boolean, default=False)
    is_ai_generated = db.Column(db.Boolean, default=False)
    source_document_id = db.Column(db.Integer, db.ForeignKey('documents.id'), nullable=True)
    color = db.Column(db.String(7), default='#7C3AED')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'content': self.content,
            'subject': self.subject,
            'tags': self.tags.split(',') if self.tags else [],
            'is_bookmarked': self.is_bookmarked,
            'is_ai_generated': self.is_ai_generated,
            'color': self.color,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }


class Task(db.Model):
    __tablename__ = 'tasks'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default='')
    subject = db.Column(db.String(100), default='General')
    priority = db.Column(db.String(20), default='medium')  # low, medium, high, urgent
    status = db.Column(db.String(20), default='pending')  # pending, in_progress, completed
    due_date = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'subject': self.subject,
            'priority': self.priority,
            'status': self.status,
            'due_date': self.due_date.isoformat() if self.due_date else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'created_at': self.created_at.isoformat()
        }


class Document(db.Model):
    __tablename__ = 'documents'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    original_name = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(255), nullable=False)
    file_type = db.Column(db.String(10), nullable=False)
    file_size = db.Column(db.Integer, default=0)
    content_text = db.Column(db.Text, default='')
    summary = db.Column(db.Text, default='')
    subject = db.Column(db.String(100), default='General')
    is_processed = db.Column(db.Boolean, default=False)
    page_count = db.Column(db.Integer, default=0)
    word_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    notes = db.relationship('Note', backref='source_document', lazy='dynamic')
    
    def to_dict(self):
        return {
            'id': self.id,
            'original_name': self.original_name,
            'file_type': self.file_type,
            'file_size': self.file_size,
            'subject': self.subject,
            'is_processed': self.is_processed,
            'page_count': self.page_count,
            'word_count': self.word_count,
            'has_summary': bool(self.summary),
            'created_at': self.created_at.isoformat()
        }


class Quiz(db.Model):
    __tablename__ = 'quizzes'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default='')
    subject = db.Column(db.String(100), default='General')
    difficulty = db.Column(db.String(20), default='medium')
    is_ai_generated = db.Column(db.Boolean, default=False)
    source_document_id = db.Column(db.Integer, db.ForeignKey('documents.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    questions = db.relationship('Question', backref='quiz', lazy='dynamic', cascade='all, delete-orphan')
    attempts = db.relationship('QuizAttempt', backref='quiz', lazy='dynamic', cascade='all, delete-orphan')
    
    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'subject': self.subject,
            'difficulty': self.difficulty,
            'is_ai_generated': self.is_ai_generated,
            'question_count': self.questions.count(),
            'created_at': self.created_at.isoformat()
        }


class Question(db.Model):
    __tablename__ = 'questions'
    
    id = db.Column(db.Integer, primary_key=True)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quizzes.id'), nullable=False)
    question_text = db.Column(db.Text, nullable=False)
    option_a = db.Column(db.Text, nullable=False)
    option_b = db.Column(db.Text, nullable=False)
    option_c = db.Column(db.Text, nullable=False)
    option_d = db.Column(db.Text, nullable=False)
    correct_answer = db.Column(db.String(1), nullable=False)  # A, B, C, or D
    explanation = db.Column(db.Text, default='')
    order_num = db.Column(db.Integer, default=0)
    
    def to_dict(self):
        return {
            'id': self.id,
            'question_text': self.question_text,
            'option_a': self.option_a,
            'option_b': self.option_b,
            'option_c': self.option_c,
            'option_d': self.option_d,
            'correct_answer': self.correct_answer,
            'explanation': self.explanation
        }


class QuizAttempt(db.Model):
    __tablename__ = 'quiz_attempts'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quizzes.id'), nullable=False)
    score = db.Column(db.Float, default=0.0)
    total_questions = db.Column(db.Integer, default=0)
    correct_answers = db.Column(db.Integer, default=0)
    time_taken = db.Column(db.Integer, default=0)  # seconds
    answers_json = db.Column(db.Text, default='{}')
    completed_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'quiz_id': self.quiz_id,
            'score': self.score,
            'total_questions': self.total_questions,
            'correct_answers': self.correct_answers,
            'time_taken': self.time_taken,
            'completed_at': self.completed_at.isoformat()
        }


class FlashcardSet(db.Model):
    __tablename__ = 'flashcard_sets'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default='')
    subject = db.Column(db.String(100), default='General')
    is_ai_generated = db.Column(db.Boolean, default=False)
    source_document_id = db.Column(db.Integer, db.ForeignKey('documents.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    flashcards = db.relationship('Flashcard', backref='set', lazy='dynamic', cascade='all, delete-orphan')
    
    def to_dict(self):
        cards = self.flashcards.all()
        mastered = sum(1 for c in cards if c.is_mastered)
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'subject': self.subject,
            'is_ai_generated': self.is_ai_generated,
            'card_count': len(cards),
            'mastered_count': mastered,
            'created_at': self.created_at.isoformat()
        }


class Flashcard(db.Model):
    __tablename__ = 'flashcards'
    
    id = db.Column(db.Integer, primary_key=True)
    set_id = db.Column(db.Integer, db.ForeignKey('flashcard_sets.id'), nullable=False)
    front = db.Column(db.Text, nullable=False)
    back = db.Column(db.Text, nullable=False)
    is_mastered = db.Column(db.Boolean, default=False)
    times_reviewed = db.Column(db.Integer, default=0)
    order_num = db.Column(db.Integer, default=0)
    
    def to_dict(self):
        return {
            'id': self.id,
            'front': self.front,
            'back': self.back,
            'is_mastered': self.is_mastered,
            'times_reviewed': self.times_reviewed
        }


class ChatMessage(db.Model):
    __tablename__ = 'chat_messages'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'user' or 'assistant'
    content = db.Column(db.Text, nullable=False)
    document_id = db.Column(db.Integer, db.ForeignKey('documents.id'), nullable=True)
    session_id = db.Column(db.String(100), default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'role': self.role,
            'content': self.content,
            'created_at': self.created_at.isoformat()
        }


class StudySession(db.Model):
    __tablename__ = 'study_sessions'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    activity_type = db.Column(db.String(50), nullable=False)  # quiz, flashcard, notes, document
    activity_id = db.Column(db.Integer, nullable=True)
    duration_minutes = db.Column(db.Float, default=0)
    subject = db.Column(db.String(100), default='General')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
