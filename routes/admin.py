from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from models import User, Note, Task, Document, Quiz, QuizAttempt, FlashcardSet, StudySession, Subscription
from extensions import db
from datetime import datetime, timedelta
from sqlalchemy import func
from routes.payment import activate_subscription, send_payment_receipt

admin_bp = Blueprint('admin', __name__)

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        if not current_user.is_admin():
            flash('Access denied. Admin privileges required.', 'error')
            return redirect(url_for('student.dashboard'))
        return f(*args, **kwargs)
    return decorated_function


@admin_bp.route('/dashboard')
@login_required
@admin_required
def dashboard():
    # Platform statistics
    total_students = User.query.filter_by(role='student').count()
    active_students = User.query.filter_by(role='student', is_active=True).count()
    total_docs = Document.query.count()
    total_notes = Note.query.count()
    total_tasks = Task.query.count()
    total_quizzes = Quiz.query.count()
    total_attempts = QuizAttempt.query.count()
    
    avg_score = db.session.query(func.avg(QuizAttempt.score)).scalar() or 0
    
    # New registrations in last 30 days
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    new_students = User.query.filter(
        User.role == 'student',
        User.created_at >= thirty_days_ago
    ).count()
    
    # Recent activity
    recent_students = User.query.filter_by(role='student').order_by(
        User.created_at.desc()
    ).limit(10).all()
    
    # Daily registrations (last 7 days)
    week_ago = datetime.utcnow() - timedelta(days=7)
    daily_regs = {}
    for i in range(7):
        day = (datetime.utcnow() - timedelta(days=i)).strftime('%Y-%m-%d')
        daily_regs[day] = 0
    
    recent_users = User.query.filter(
        User.created_at >= week_ago,
        User.role == 'student'
    ).all()
    for u in recent_users:
        day = u.created_at.strftime('%Y-%m-%d')
        if day in daily_regs:
            daily_regs[day] += 1
    
    # AI usage (quiz + notes + chat activity)
    ai_notes = Note.query.filter_by(is_ai_generated=True).count()
    ai_quizzes = Quiz.query.filter_by(is_ai_generated=True).count()
    ai_fc_sets = FlashcardSet.query.filter_by(is_ai_generated=True).count()
    
    return render_template('admin/dashboard.html',
                           total_students=total_students,
                           active_students=active_students,
                           total_docs=total_docs,
                           total_notes=total_notes,
                           total_tasks=total_tasks,
                           total_quizzes=total_quizzes,
                           total_attempts=total_attempts,
                           avg_score=round(float(avg_score), 1),
                           new_students=new_students,
                           recent_students=recent_students,
                           daily_regs=daily_regs,
                           ai_notes=ai_notes,
                           ai_quizzes=ai_quizzes,
                           ai_fc_sets=ai_fc_sets)


@admin_bp.route('/students')
@login_required
@admin_required
def students():
    search = request.args.get('search', '')
    status_filter = request.args.get('status', '')
    
    query = User.query.filter_by(role='student')
    
    if search:
        query = query.filter(
            (User.full_name.ilike(f'%{search}%')) |
            (User.email.ilike(f'%{search}%')) |
            (User.username.ilike(f'%{search}%'))
        )
    if status_filter == 'active':
        query = query.filter_by(is_active=True)
    elif status_filter == 'inactive':
        query = query.filter_by(is_active=False)
    
    students_list = query.order_by(User.created_at.desc()).all()
    
    return render_template('admin/students.html', 
                           students=students_list, 
                           search=search,
                           status_filter=status_filter)


@admin_bp.route('/students/<int:user_id>')
@login_required
@admin_required
def student_detail(user_id):
    student = User.query.filter_by(id=user_id, role='student').first_or_404()
    
    stats = {
        'notes': student.notes.count(),
        'tasks': student.tasks.count(),
        'completed_tasks': student.tasks.filter_by(status='completed').count(),
        'quizzes': student.quizzes.count(),
        'quiz_attempts': student.quiz_attempts.count(),
        'documents': student.documents.count(),
        'flashcard_sets': student.flashcard_sets.count(),
    }
    
    avg_score_result = db.session.query(func.avg(QuizAttempt.score)).filter_by(user_id=user_id).scalar()
    stats['avg_score'] = round(float(avg_score_result or 0), 1)
    
    recent_attempts = student.quiz_attempts.order_by(QuizAttempt.completed_at.desc()).limit(5).all()
    
    return render_template('admin/student_detail.html', 
                           student=student, stats=stats,
                           recent_attempts=recent_attempts)


@admin_bp.route('/students/<int:user_id>/toggle-status', methods=['POST'])
@login_required
@admin_required
def toggle_student_status(user_id):
    student = User.query.filter_by(id=user_id, role='student').first_or_404()
    student.is_active = not student.is_active
    db.session.commit()
    
    status = 'activated' if student.is_active else 'deactivated'
    flash(f'Student account {status} successfully.', 'success')
    return redirect(url_for('admin.students'))


@admin_bp.route('/students/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_student(user_id):
    student = User.query.filter_by(id=user_id, role='student').first_or_404()
    db.session.delete(student)
    db.session.commit()
    flash('Student account deleted permanently.', 'info')
    return redirect(url_for('admin.students'))


@admin_bp.route('/content')
@login_required
@admin_required
def content():
    # Overview of all platform content
    notes = Note.query.order_by(Note.created_at.desc()).limit(20).all()
    quizzes = Quiz.query.order_by(Quiz.created_at.desc()).limit(20).all()
    documents = Document.query.order_by(Document.created_at.desc()).limit(20).all()
    
    return render_template('admin/content.html',
                           notes=notes, quizzes=quizzes, documents=documents)


@admin_bp.route('/content/note/<int:note_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_note(note_id):
    note = Note.query.get_or_404(note_id)
    db.session.delete(note)
    db.session.commit()
    flash('Note deleted by admin.', 'info')
    return redirect(url_for('admin.content'))


@admin_bp.route('/content/quiz/<int:quiz_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_quiz(quiz_id):
    quiz = Quiz.query.get_or_404(quiz_id)
    db.session.delete(quiz)
    db.session.commit()
    flash('Quiz deleted by admin.', 'info')
    return redirect(url_for('admin.content'))


@admin_bp.route('/stats')
@login_required
@admin_required
def stats():
    # Comprehensive platform analytics
    # Subject distribution
    subject_data = db.session.query(Note.subject, func.count(Note.id)).group_by(Note.subject).all()
    
    # Quiz difficulty distribution
    difficulty_data = db.session.query(Quiz.difficulty, func.count(Quiz.id)).group_by(Quiz.difficulty).all()
    
    # Monthly registrations (last 6 months)
    monthly_data = []
    for i in range(5, -1, -1):
        month_start = datetime.utcnow().replace(day=1) - timedelta(days=30*i)
        month_end = month_start + timedelta(days=31)
        count = User.query.filter(
            User.role == 'student',
            User.created_at >= month_start,
            User.created_at < month_end
        ).count()
        monthly_data.append({'month': month_start.strftime('%b %Y'), 'count': count})
    
    return render_template('admin/stats.html',
                           subject_data=subject_data,
                           difficulty_data=difficulty_data,
                           monthly_data=monthly_data)


# ─── Payments (manual UPI confirmation) ──────────────────────────────────────

@admin_bp.route('/payments')
@login_required
@admin_required
def payments():
    pending = Subscription.query.filter_by(payment_status='submitted').order_by(Subscription.created_at.desc()).all()
    recent = Subscription.query.filter(Subscription.payment_status.in_(['paid', 'failed'])) \
        .order_by(Subscription.created_at.desc()).limit(30).all()
    ids = {x.user_id for x in pending + recent if x.user_id}
    users = {u.id: u for u in User.query.filter(User.id.in_(ids)).all()} if ids else {}
    return render_template('admin/payments.html', pending=pending, recent=recent, users=users)


@admin_bp.route('/payments/<int:sub_id>/approve', methods=['POST'])
@login_required
@admin_required
def approve_payment(sub_id):
    sub = Subscription.query.get_or_404(sub_id)
    user = User.query.get(sub.user_id) if sub.user_id else None
    if sub.payment_status != 'submitted' or not user:
        flash('This payment is not awaiting approval.', 'error')
        return redirect(url_for('admin.payments'))
    activate_subscription(sub, user)
    db.session.commit()
    send_payment_receipt(sub, user)
    flash(f'Plan activated for {user.full_name}.', 'success')
    return redirect(url_for('admin.payments'))


@admin_bp.route('/payments/<int:sub_id>/reject', methods=['POST'])
@login_required
@admin_required
def reject_payment(sub_id):
    sub = Subscription.query.get_or_404(sub_id)
    if sub.payment_status != 'submitted':
        flash('This payment is not awaiting approval.', 'error')
    else:
        sub.payment_status = 'failed'
        db.session.commit()
        flash('Payment rejected.', 'success')
    return redirect(url_for('admin.payments'))


@admin_bp.route('/students/<int:user_id>/reset-password', methods=['POST'])
@login_required
@admin_required
def reset_student_password(user_id):
    import secrets
    user = User.query.filter_by(id=user_id, role='student').first_or_404()
    temp = secrets.token_urlsafe(9) + 'aA1!'
    user.set_password(temp)
    db.session.commit()
    flash(f'Temporary password for {user.email}: {temp} — share it privately and ask the student to change it in Profile.', 'success')
    return redirect(url_for('admin.student_detail', user_id=user.id))
