import os
import uuid
import json
import io
import re
import tempfile
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app, abort, send_from_directory
from flask_login import login_required, current_user
from models import User, Note, Task, Document, Quiz, Question, QuizAttempt, FlashcardSet, Flashcard, ChatMessage, StudySession
from extensions import db
from sqlalchemy import func
from werkzeug.utils import secure_filename

student_bp = Blueprint('student', __name__)

ALLOWED_EXTENSIONS = {'pdf', 'txt', 'docx', 'md', 'png', 'jpg', 'jpeg', 'webp'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _supabase_storage_api_url(path):
    base_url = current_app.config['SUPABASE_URL']
    return f"{base_url}/storage/v1/{path.lstrip('/')}"


def _supabase_storage_headers():
    service_key = current_app.config['SUPABASE_SERVICE_ROLE_KEY']
    return {
        'apikey': service_key,
        'Authorization': f'Bearer {service_key}'
    }


def _supabase_signed_url(value):
    base_url = current_app.config['SUPABASE_URL']
    if value.startswith(('https://', 'http://')):
        return value
    if value.startswith('/storage/v1/'):
        return f'{base_url}{value}'
    return f'{base_url}/storage/v1{value if value.startswith("/") else "/" + value}'


def create_supabase_upload_url(object_key):
    import httpx
    from urllib.parse import quote

    bucket = quote(current_app.config['SUPABASE_STORAGE_BUCKET'], safe='')
    key = quote(object_key, safe='/')
    response = httpx.post(
        _supabase_storage_api_url(f'object/upload/sign/{bucket}/{key}'),
        headers=_supabase_storage_headers(),
        json={},
        timeout=30
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        current_app.logger.error('Supabase signed upload URL request failed with status %s', response.status_code)
        raise

    signed_path = response.json().get('url')
    if not signed_path:
        raise RuntimeError('Supabase did not return a signed upload URL.')
    return _supabase_signed_url(signed_path)


def create_supabase_download_url(object_key):
    import httpx
    from urllib.parse import quote

    bucket = quote(current_app.config['SUPABASE_STORAGE_BUCKET'], safe='')
    key = quote(object_key, safe='/')
    response = httpx.post(
        _supabase_storage_api_url(f'object/sign/{bucket}/{key}'),
        headers=_supabase_storage_headers(),
        json={'expiresIn': 120},
        timeout=30
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        current_app.logger.error('Supabase signed download URL request failed with status %s', response.status_code)
        raise

    signed_path = response.json().get('signedURL')
    if not signed_path:
        raise RuntimeError('Supabase did not return a signed download URL.')
    return _supabase_signed_url(signed_path)


def download_supabase_object(object_key, suffix):
    import httpx
    from urllib.parse import quote

    bucket = quote(current_app.config['SUPABASE_STORAGE_BUCKET'], safe='')
    key = quote(object_key, safe='/')
    local_file = tempfile.NamedTemporaryFile(prefix='smartstudy-', suffix=suffix, delete=False)
    local_path = local_file.name
    try:
        with local_file, httpx.stream(
            'GET',
            _supabase_storage_api_url(f'object/{bucket}/{key}'),
            headers=_supabase_storage_headers(),
            timeout=httpx.Timeout(120, connect=15)
        ) as response:
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError:
                current_app.logger.error(
                    'Supabase object download failed with status %s',
                    response.status_code
                )
                raise
            downloaded_size = 0
            for chunk in response.iter_bytes():
                local_file.write(chunk)
                downloaded_size += len(chunk)
        return local_path, downloaded_size
    except Exception:
        if os.path.exists(local_path):
            os.remove(local_path)
        raise


def delete_supabase_object(object_key):
    import httpx
    from urllib.parse import quote

    bucket = quote(current_app.config['SUPABASE_STORAGE_BUCKET'], safe='')
    response = httpx.delete(
        _supabase_storage_api_url(f'object/{bucket}'),
        headers=_supabase_storage_headers(),
        json={'prefixes': [object_key]},
        timeout=30
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError:
        current_app.logger.error('Supabase object deletion failed with status %s', response.status_code)
        raise


def extract_document_content(file_path, ext, subject, filename):
    content_text = ''
    page_count = 0
    processing_error = None

    try:
        if ext == 'pdf':
            import pypdf
            with open(file_path, 'rb') as uploaded_file:
                reader = pypdf.PdfReader(uploaded_file)
                if reader.is_encrypted:
                    raise ValueError('This PDF is password-protected. Remove its password and upload it again.')
                page_count = len(reader.pages)
                page_texts = []
                sparse_pages = []
                for page_number, page in enumerate(reader.pages, start=1):
                    try:
                        page_text = (page.extract_text(extraction_mode='layout') or '').strip()
                    except Exception:
                        current_app.logger.info(
                            'Text extraction failed for PDF page %s in %s',
                            page_number, filename, exc_info=True
                        )
                        page_text = ''
                    page_texts.append(page_text)
                    try:
                        has_embedded_images = bool(page.images)
                    except Exception:
                        current_app.logger.info(
                            'Image detection failed for PDF page %s in %s',
                            page_number, filename, exc_info=True
                        )
                        has_embedded_images = False
                    if len(page_text) < 40 or has_embedded_images:
                        sparse_pages.append(page_number)

            visual_analysis = ''
            if sparse_pages:
                transcription, processing_error = transcribe_scanned_pdf(
                    file_path, subject, filename, sparse_pages
                )
                if transcription:
                    visual_analysis = '[Visual analysis of scanned or image-heavy pages]\n' + transcription
            content_text = '\n\n'.join(
                f'[Page {page_number}]\n{page_text}'
                for page_number, page_text in enumerate(page_texts, start=1)
                if page_text
            )
            if visual_analysis:
                content_text = '\n\n'.join(
                    part for part in (content_text, visual_analysis) if part
                )
            if not content_text.strip() and not processing_error:
                processing_error = 'No readable text or visual content could be extracted from this PDF.'
        elif ext in ('txt', 'md'):
            for encoding in ('utf-8', 'latin-1', 'cp1252', 'ascii'):
                try:
                    with open(file_path, 'r', encoding=encoding) as uploaded_file:
                        content_text = uploaded_file.read()
                    break
                except (UnicodeDecodeError, UnicodeError):
                    continue
            page_count = 1
        elif ext == 'docx':
            from docx import Document as DocxDocument
            doc_obj = DocxDocument(file_path)
            paragraphs = [paragraph.text for paragraph in doc_obj.paragraphs if paragraph.text.strip()]
            content_text = '\n'.join(paragraphs)
            page_count = max(1, len(content_text) // 3000)
        elif ext in ('png', 'jpg', 'jpeg', 'webp'):
            page_count = 1
            from routes.api import ai_error_response, generate_vision_response, require_ai_client
            client, model, error = require_ai_client()
            if error:
                processing_error = 'Image analysis requires OPENROUTER_API_KEY.'
            else:
                with open(file_path, 'rb') as image_file:
                    image_data = image_file.read()
                mime = 'image/jpeg' if ext in ('jpg', 'jpeg') else f'image/{ext}'
                try:
                    content_text = generate_vision_response(
                        client,
                        model,
                        (
                            f"You are an expert academic tutor for {subject}.\n"
                            "Analyze this study image carefully. Transcribe visible text, formulas, "
                            "equations, and labels; explain diagrams, charts, graphs, and relationships; "
                            "then give evidence-based study takeaways. Mark illegible details as unclear "
                            "instead of guessing. Do not claim certainty where the image is ambiguous."
                        ),
                        [(mime, image_data)],
                        max_tokens=2500
                    )
                    if not content_text.strip():
                        processing_error = 'The AI model returned no image analysis.'
                except Exception as exc:
                    result, _ = ai_error_response(exc, 'Image analysis')
                    processing_error = result.get_json().get('error', 'Image analysis failed.')
                    current_app.logger.exception('Image analysis failed for %s', filename)
        else:
            processing_error = 'This file type is not supported.'
    except Exception as exc:
        current_app.logger.exception('Failed to extract uploaded document %s', filename)
        processing_error = str(exc) or 'Could not extract content from this document.'

    return content_text, page_count, processing_error


def save_processed_document(file_path, original_name, stored_name, ext, subject, file_size):
    content_text, page_count, processing_error = extract_document_content(
        file_path, ext, subject, original_name
    )
    document = Document(
        user_id=current_user.id,
        original_name=original_name,
        stored_name=stored_name,
        file_type=ext,
        file_size=file_size,
        content_text=content_text,
        subject=subject,
        is_processed=bool(content_text and not processing_error),
        page_count=page_count,
        word_count=len(content_text.split()) if content_text else 0
    )
    db.session.add(document)
    db.session.add(StudySession(
        user_id=current_user.id,
        activity_type='document',
        subject=subject
    ))
    db.session.commit()
    return document, processing_error


def transcribe_scanned_pdf(file_path, subject, filename, page_numbers):
    """Transcribe and describe sparse PDF pages with OpenRouter vision."""
    from routes.api import ai_error_response, generate_vision_response, require_ai_client

    page_limit = current_app.config['SCANNED_PDF_PAGE_LIMIT']
    if len(page_numbers) > page_limit:
        return '', (
            f'This PDF has {len(page_numbers)} scanned or image-heavy pages; '
            f'the current limit is {page_limit}. Increase SCANNED_PDF_PAGE_LIMIT '
            'to process the complete document.'
        )

    client, model, error = require_ai_client()
    if error:
        return '', 'Scanned PDF pages require OPENROUTER_API_KEY for transcription and visual analysis.'

    try:
        import pypdfium2
        pdf = pypdfium2.PdfDocument(file_path)
    except ImportError:
        return '', 'Cannot process scanned PDF pages because pypdfium2 is unavailable.'
    except Exception:
        current_app.logger.exception('Failed to render scanned PDF %s', filename)
        return '', 'The PDF renderer could not open this document.'

    transcriptions = []
    try:
        for offset in range(0, len(page_numbers), 4):
            batch_numbers = page_numbers[offset:offset + 4]
            image_data = []
            for page_number in batch_numbers:
                image = pdf[page_number - 1].render(scale=1.8).to_pil()
                try:
                    image_buffer = io.BytesIO()
                    image.save(image_buffer, format='JPEG', quality=90)
                    image_data.append(('image/jpeg', image_buffer.getvalue()))
                finally:
                    image.close()

            prompt = (
                f'Analyze these pages from a {subject} study document in the supplied order. '
                'For each page, start with its exact page heading (for example, "Page 4"). '
                'Transcribe visible text, equations, symbols, and table values as accurately as possible. '
                'Also describe relevant diagrams, charts, and visual relationships for a student. '
                'Mark anything illegible as [unclear]; do not guess or invent missing content.'
            )
            transcription = generate_vision_response(
                client,
                model,
                prompt,
                image_data,
                max_tokens=min(1200 * len(image_data), 4800)
            ).strip()
            if not transcription:
                return '', f'OpenRouter returned no transcription for PDF pages {batch_numbers[0]}-{batch_numbers[-1]}.'
            transcriptions.append(transcription)
    except Exception as exc:
        result, status = ai_error_response(exc, 'Scanned PDF transcription')
        current_app.logger.warning('Vision PDF transcription failed with status %s', status)
        return '', result.get_json().get('error', 'Scanned PDF transcription failed.')
    finally:
        pdf.close()

    return '\n\n'.join(transcriptions), None


@student_bp.app_context_processor
def inject_now():
    return {'now': datetime.utcnow()}


# ─── Dashboard ─────────────────────────────────────────────────────────────────

@student_bp.route('/dashboard')
@login_required
def dashboard():
    if current_user.is_admin():
        return redirect(url_for('admin.dashboard'))
    
    # Stats
    total_notes = current_user.notes.count()
    total_tasks = current_user.tasks.count()
    completed_tasks = current_user.tasks.filter_by(status='completed').count()
    total_quizzes = current_user.quizzes.count()
    total_docs = current_user.documents.count()
    total_flashcard_sets = current_user.flashcard_sets.count()
    
    # Recent quiz performance
    recent_attempts = current_user.quiz_attempts.order_by(QuizAttempt.completed_at.desc()).limit(5).all()
    avg_score = 0
    if recent_attempts:
        avg_score = sum(a.score for a in recent_attempts) / len(recent_attempts)
    
    # Recent notes
    recent_notes = current_user.notes.order_by(Note.updated_at.desc()).limit(4).all()
    
    # Upcoming tasks
    upcoming_tasks = current_user.tasks.filter(
        Task.status != 'completed',
        Task.due_date >= datetime.utcnow()
    ).order_by(Task.due_date.asc()).limit(5).all()
    
    # Study streak (simplified - count days with activity in last 7 days)
    week_ago = datetime.utcnow() - timedelta(days=7)
    recent_activity = current_user.study_sessions.filter(
        StudySession.created_at >= week_ago
    ).all()
    active_days = len(set(s.created_at.date() for s in recent_activity))
    
    # Recent documents
    recent_docs = current_user.documents.order_by(Document.created_at.desc()).limit(3).all()
    
    stats = {
        'total_notes': total_notes,
        'total_tasks': total_tasks,
        'completed_tasks': completed_tasks,
        'task_completion_rate': round((completed_tasks / total_tasks * 100) if total_tasks else 0, 1),
        'total_quizzes': total_quizzes,
        'total_docs': total_docs,
        'total_flashcard_sets': total_flashcard_sets,
        'avg_quiz_score': round(avg_score, 1),
        'active_days': active_days,
        'total_quiz_attempts': current_user.quiz_attempts.count()
    }
    
    return render_template('student/dashboard.html',
                           stats=stats,
                           recent_notes=recent_notes,
                           upcoming_tasks=upcoming_tasks,
                           recent_attempts=recent_attempts,
                           recent_docs=recent_docs)


# ─── Notes ─────────────────────────────────────────────────────────────────────

@student_bp.route('/notes')
@login_required
def notes():
    search = request.args.get('search', '')
    subject_filter = request.args.get('subject', '')
    bookmarked = request.args.get('bookmarked', '')
    
    query = current_user.notes
    
    if search:
        query = query.filter(
            (Note.title.ilike(f'%{search}%')) | (Note.content.ilike(f'%{search}%'))
        )
    if subject_filter:
        query = query.filter_by(subject=subject_filter)
    if bookmarked == '1':
        query = query.filter_by(is_bookmarked=True)
    
    notes_list = query.order_by(Note.updated_at.desc()).all()
    subjects = db.session.query(Note.subject).filter_by(user_id=current_user.id).distinct().all()
    subjects = [s[0] for s in subjects if s[0]]
    
    return render_template('student/notes.html', notes=notes_list, subjects=subjects,
                           search=search, subject_filter=subject_filter, bookmarked=bookmarked)


@student_bp.route('/notes/create', methods=['GET', 'POST'])
@login_required
def create_note():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        subject = request.form.get('subject', 'General').strip()
        tags = request.form.get('tags', '').strip()
        color = request.form.get('color', '#7C3AED')
        
        if not title:
            flash('Note title is required.', 'error')
            return redirect(url_for('student.create_note'))
        
        note = Note(
            user_id=current_user.id,
            title=title,
            content=content,
            subject=subject,
            tags=tags,
            color=color
        )
        db.session.add(note)
        
        # Log study session
        session_log = StudySession(user_id=current_user.id, activity_type='notes', subject=subject)
        db.session.add(session_log)
        db.session.commit()
        
        flash('Note created successfully!', 'success')
        return redirect(url_for('student.notes'))
    
    return render_template('student/note_editor.html', note=None, mode='create')


@student_bp.route('/notes/<int:note_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_note(note_id):
    note = Note.query.filter_by(id=note_id, user_id=current_user.id).first_or_404()
    
    if request.method == 'POST':
        note.title = request.form.get('title', '').strip() or note.title
        note.content = request.form.get('content', '').strip()
        note.subject = request.form.get('subject', 'General').strip()
        note.tags = request.form.get('tags', '').strip()
        note.color = request.form.get('color', note.color)
        note.updated_at = datetime.utcnow()
        db.session.commit()
        
        flash('Note updated successfully!', 'success')
        return redirect(url_for('student.notes'))
    
    return render_template('student/note_editor.html', note=note, mode='edit')


@student_bp.route('/notes/<int:note_id>/delete', methods=['POST'])
@login_required
def delete_note(note_id):
    note = Note.query.filter_by(id=note_id, user_id=current_user.id).first_or_404()
    db.session.delete(note)
    db.session.commit()
    flash('Note deleted.', 'info')
    return redirect(url_for('student.notes'))


@student_bp.route('/notes/<int:note_id>/toggle-bookmark', methods=['POST'])
@login_required
def toggle_bookmark(note_id):
    note = Note.query.filter_by(id=note_id, user_id=current_user.id).first_or_404()
    note.is_bookmarked = not note.is_bookmarked
    db.session.commit()
    return jsonify({'bookmarked': note.is_bookmarked})


# ─── Tasks / Planner ───────────────────────────────────────────────────────────

@student_bp.route('/planner')
@login_required
def planner():
    filter_status = request.args.get('status', '')
    filter_priority = request.args.get('priority', '')
    filter_subject = request.args.get('subject', '')
    
    query = current_user.tasks
    if filter_status:
        query = query.filter_by(status=filter_status)
    if filter_priority:
        query = query.filter_by(priority=filter_priority)
    if filter_subject:
        query = query.filter_by(subject=filter_subject)
    
    tasks = query.order_by(Task.due_date.asc().nullsfirst(), Task.created_at.desc()).all()
    subjects = db.session.query(Task.subject).filter_by(user_id=current_user.id).distinct().all()
    subjects = [s[0] for s in subjects if s[0]]
    
    # Group by status
    pending = [t for t in tasks if t.status == 'pending']
    in_progress = [t for t in tasks if t.status == 'in_progress']
    completed = [t for t in tasks if t.status == 'completed']
    
    return render_template('student/planner.html', 
                           tasks=tasks, pending=pending,
                           in_progress=in_progress, completed=completed,
                           subjects=subjects,
                           filter_status=filter_status,
                           filter_priority=filter_priority,
                           filter_subject=filter_subject)


@student_bp.route('/planner/task/create', methods=['POST'])
@login_required
def create_task():
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    subject = request.form.get('subject', 'General').strip()
    priority = request.form.get('priority', 'medium')
    due_date_str = request.form.get('due_date', '')
    
    if not title:
        flash('Task title is required.', 'error')
        return redirect(url_for('student.planner'))
    
    due_date = None
    if due_date_str:
        try:
            due_date = datetime.strptime(due_date_str, '%Y-%m-%dT%H:%M')
        except ValueError:
            try:
                due_date = datetime.strptime(due_date_str, '%Y-%m-%d')
            except ValueError:
                pass
    
    task = Task(
        user_id=current_user.id,
        title=title,
        description=description,
        subject=subject,
        priority=priority,
        due_date=due_date
    )
    db.session.add(task)
    db.session.commit()
    
    flash('Task added successfully!', 'success')
    return redirect(url_for('student.planner'))


@student_bp.route('/planner/task/<int:task_id>/update-status', methods=['POST'])
@login_required
def update_task_status(task_id):
    task = Task.query.filter_by(id=task_id, user_id=current_user.id).first_or_404()
    new_status = request.form.get('status', task.status)
    
    valid_statuses = ['pending', 'in_progress', 'completed']
    if new_status in valid_statuses:
        task.status = new_status
        if new_status == 'completed':
            task.completed_at = datetime.utcnow()
        else:
            task.completed_at = None
        db.session.commit()
        flash('Task status updated.', 'success')
    
    return redirect(url_for('student.planner'))


@student_bp.route('/planner/task/<int:task_id>/delete', methods=['POST'])
@login_required
def delete_task(task_id):
    task = Task.query.filter_by(id=task_id, user_id=current_user.id).first_or_404()
    db.session.delete(task)
    db.session.commit()
    flash('Task deleted.', 'info')
    return redirect(url_for('student.planner'))


# ─── Documents / AI Workspace ──────────────────────────────────────────────────

@student_bp.route('/workspace')
@login_required
def workspace():
    docs = current_user.documents.order_by(Document.created_at.desc()).all()
    return render_template('student/workspace.html', documents=docs)


@student_bp.route('/workspace/upload-url', methods=['POST'])
@login_required
def create_document_upload_url():
    if not current_app.config['SUPABASE_STORAGE_ENABLED']:
        return jsonify({'error': 'Direct uploads are not configured. Configure Supabase Storage on the server.'}), 503

    data = request.get_json(silent=True) or {}
    original_name = secure_filename(str(data.get('filename', '')).strip())
    if not original_name or not allowed_file(original_name):
        return jsonify({'error': 'Unsupported file type. Use PDF, TXT, DOCX, Markdown, PNG, JPG, or WEBP.'}), 400

    ext = original_name.rsplit('.', 1)[1].lower()
    object_key = f'{current_user.id}/{uuid.uuid4().hex}.{ext}'
    try:
        signed_url = create_supabase_upload_url(object_key)
    except Exception:
        current_app.logger.exception('Could not create Supabase upload URL for user %s', current_user.id)
        return jsonify({'error': 'Could not prepare the storage upload. Check Supabase Storage configuration and try again.'}), 502

    return jsonify({
        'signed_url': signed_url,
        'object_key': object_key,
        'content_type': {
            'pdf': 'application/pdf',
            'txt': 'text/plain',
            'md': 'text/markdown',
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'png': 'image/png',
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'webp': 'image/webp'
        }[ext],
        'filename': original_name
    })


@student_bp.route('/workspace/upload-complete', methods=['POST'])
@login_required
def complete_document_upload():
    if not current_app.config['SUPABASE_STORAGE_ENABLED']:
        return jsonify({'error': 'Supabase Storage is not configured on the server.'}), 503

    data = request.get_json(silent=True) or {}
    object_key = str(data.get('object_key', ''))
    filename = secure_filename(str(data.get('filename', '')).strip())
    if not filename or not allowed_file(filename):
        return jsonify({'error': 'Unsupported file type.'}), 400

    ext = filename.rsplit('.', 1)[1].lower()
    expected_key = rf'{current_user.id}/[0-9a-f]{{32}}\.{re.escape(ext)}'
    if not re.fullmatch(expected_key, object_key):
        return jsonify({'error': 'Invalid or unauthorized storage object.'}), 400

    local_path = None
    try:
        local_path, file_size = download_supabase_object(object_key, f'.{ext}')
        client_size = data.get('file_size')
        if not isinstance(client_size, int) or client_size <= 0 or client_size != file_size:
            return jsonify({'error': 'The uploaded file size could not be verified. Please retry the upload.'}), 400

        subject = str(data.get('subject') or 'General').strip()[:100] or 'General'
        document, processing_error = save_processed_document(
            local_path, filename, f'supabase:{object_key}', ext, subject, file_size
        )
    except Exception:
        current_app.logger.exception('Could not process Supabase upload for user %s', current_user.id)
        return jsonify({'error': 'The file was uploaded but could not be processed. Check its format and try again.'}), 502
    finally:
        if local_path and os.path.exists(local_path):
            os.remove(local_path)

    if processing_error:
        flash(processing_error, 'warning')
    return jsonify({
        'success': True,
        'document_url': url_for('student.document_view', doc_id=document.id),
        'warning': processing_error
    })


@student_bp.route('/workspace/upload', methods=['POST'])
@login_required
def upload_document():
    if 'file' not in request.files:
        flash('No file selected.', 'error')
        return redirect(url_for('student.workspace'))
    
    file = request.files['file']
    subject = request.form.get('subject', 'General').strip()
    
    if not file or file.filename == '':
        flash('No file selected.', 'error')
        return redirect(url_for('student.workspace'))
    
    if not allowed_file(file.filename):
        flash('File type not supported. Please upload PDF, TXT, DOCX, or MD files.', 'error')
        return redirect(url_for('student.workspace'))

    file.stream.seek(0, os.SEEK_END)
    file_size = file.stream.tell()
    file.stream.seek(0)
    max_file_size = current_app.config['MAX_UPLOAD_FILE_SIZE']
    if file_size > max_file_size:
        flash(f'File too large. Maximum file size is {max_file_size // (1024 * 1024)} MB.', 'error')
        return redirect(url_for('student.workspace'))
    
    ext = file.filename.rsplit('.', 1)[1].lower()
    stored_name = f"{current_user.id}_{uuid.uuid4().hex}.{ext}"
    upload_path = current_app.config['UPLOAD_FOLDER']
    user_upload_dir = os.path.join(upload_path, str(current_user.id))
    file_path = os.path.join(user_upload_dir, stored_name)
    try:
        os.makedirs(user_upload_dir, exist_ok=True)
        file.save(file_path)
    except OSError:
        current_app.logger.exception('Failed to store uploaded document for user %s', current_user.id)
        flash('Could not save the uploaded file. Please try again.', 'error')
        return redirect(url_for('student.workspace'))
    
    try:
        document, processing_error = save_processed_document(
            file_path, secure_filename(file.filename), stored_name, ext, subject, file_size
        )
    except Exception:
        current_app.logger.exception('Failed to process uploaded file for user %s', current_user.id)
        flash('The uploaded file could not be processed. Please check the file and try again.', 'error')
        return redirect(url_for('student.workspace'))
    
    if processing_error:
        flash(processing_error, 'warning')
    else:
        flash(f'Document "{file.filename}" uploaded and processed successfully!', 'success')
    return redirect(url_for('student.document_view', doc_id=document.id))


@student_bp.route('/workspace/document/<int:doc_id>')
@login_required
def document_view(doc_id):
    doc = Document.query.filter_by(id=doc_id, user_id=current_user.id).first_or_404()
    related_notes = Note.query.filter_by(user_id=current_user.id, source_document_id=doc_id).all()
    related_quizzes = Quiz.query.filter_by(user_id=current_user.id, source_document_id=doc_id).all()
    related_flashcards = FlashcardSet.query.filter_by(user_id=current_user.id, source_document_id=doc_id).all()
    
    return render_template('student/document_view.html', 
                           doc=doc,
                           related_notes=related_notes,
                           related_quizzes=related_quizzes,
                           related_flashcards=related_flashcards)


@student_bp.route('/workspace/file/<int:doc_id>')
@login_required
def serve_document_file(doc_id):
    """Serve uploaded document, PDF, or image file securely for inline preview."""
    doc = Document.query.filter_by(id=doc_id, user_id=current_user.id).first_or_404()
    if doc.stored_name.startswith('supabase:'):
        if not current_app.config['SUPABASE_STORAGE_ENABLED']:
            return jsonify({'error': 'Supabase Storage is not configured on the server.'}), 503
        object_key = doc.stored_name.removeprefix('supabase:')
        try:
            response = redirect(create_supabase_download_url(object_key))
        except Exception:
            current_app.logger.exception('Could not create document preview URL for document %s', doc.id)
            return jsonify({'error': 'The original document could not be loaded from storage.'}), 502
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        return response

    user_upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], str(current_user.id))
    file_path = os.path.join(user_upload_dir, doc.stored_name)
    if not os.path.isfile(file_path):
        return jsonify({
            'error': 'The original upload is no longer available on this server. Its extracted text is still available below.'
        }), 404

    mimetype = None
    if doc.file_type == 'pdf':
        mimetype = 'application/pdf'
    elif doc.file_type in ('png', 'webp'):
        mimetype = f'image/{doc.file_type}'
    elif doc.file_type in ('jpg', 'jpeg'):
        mimetype = 'image/jpeg'
    elif doc.file_type in ('txt', 'md'):
        mimetype = 'text/plain; charset=utf-8'
    response = send_from_directory(
        user_upload_dir, doc.stored_name, mimetype=mimetype, as_attachment=False
    )
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return response


@student_bp.route('/workspace/document/<int:doc_id>/delete', methods=['POST'])
@login_required
def delete_document(doc_id):
    doc = Document.query.filter_by(id=doc_id, user_id=current_user.id).first_or_404()

    if doc.stored_name.startswith('supabase:'):
        if not current_app.config['SUPABASE_STORAGE_ENABLED']:
            flash('Supabase Storage is not configured; the document was not deleted.', 'error')
            return redirect(url_for('student.workspace'))
        try:
            delete_supabase_object(doc.stored_name.removeprefix('supabase:'))
        except Exception:
            current_app.logger.exception('Could not delete stored document %s', doc.id)
            flash('The original file could not be deleted from storage. The document was not removed.', 'error')
            return redirect(url_for('student.workspace'))
    else:
        user_upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], str(current_user.id))
        file_path = os.path.join(user_upload_dir, doc.stored_name)
        if os.path.exists(file_path):
            os.remove(file_path)
    
    db.session.delete(doc)
    db.session.commit()
    
    flash('Document deleted.', 'info')
    return redirect(url_for('student.workspace'))


# ─── Quizzes ───────────────────────────────────────────────────────────────────

@student_bp.route('/quizzes')
@login_required
def quizzes():
    quizzes_list = current_user.quizzes.order_by(Quiz.created_at.desc()).all()
    attempts = current_user.quiz_attempts.order_by(QuizAttempt.completed_at.desc()).limit(10).all()
    return render_template('student/quizzes.html', quizzes=quizzes_list, attempts=attempts)


@student_bp.route('/quizzes/<int:quiz_id>/take')
@login_required
def take_quiz(quiz_id):
    quiz = Quiz.query.filter_by(id=quiz_id, user_id=current_user.id).first_or_404()
    questions = quiz.questions.order_by(Question.order_num).all()
    
    if not questions:
        flash('This quiz has no questions yet.', 'warning')
        return redirect(url_for('student.quizzes'))
    
    session_log = StudySession(user_id=current_user.id, activity_type='quiz', 
                                activity_id=quiz_id, subject=quiz.subject)
    db.session.add(session_log)
    db.session.commit()
    
    return render_template('student/take_quiz.html', quiz=quiz, questions=questions)


@student_bp.route('/quizzes/<int:quiz_id>/submit', methods=['POST'])
@login_required
def submit_quiz(quiz_id):
    quiz = Quiz.query.filter_by(id=quiz_id, user_id=current_user.id).first_or_404()
    questions = quiz.questions.order_by(Question.order_num).all()
    
    user_answers = {}
    correct = 0
    
    for q in questions:
        answer = request.form.get(f'q_{q.id}', '')
        user_answers[str(q.id)] = answer
        if answer.upper() == q.correct_answer.upper():
            correct += 1
    
    total = len(questions)
    score = (correct / total * 100) if total else 0
    time_taken = int(request.form.get('time_taken', 0))
    
    attempt = QuizAttempt(
        user_id=current_user.id,
        quiz_id=quiz_id,
        score=score,
        total_questions=total,
        correct_answers=correct,
        time_taken=time_taken,
        answers_json=json.dumps(user_answers)
    )
    db.session.add(attempt)
    db.session.commit()
    
    return redirect(url_for('student.quiz_result', attempt_id=attempt.id))


@student_bp.route('/quizzes/result/<int:attempt_id>')
@login_required
def quiz_result(attempt_id):
    attempt = QuizAttempt.query.filter_by(id=attempt_id, user_id=current_user.id).first_or_404()
    quiz = attempt.quiz
    questions = quiz.questions.order_by(Question.order_num).all()
    user_answers = json.loads(attempt.answers_json)
    
    return render_template('student/quiz_result.html',
                           attempt=attempt, quiz=quiz,
                           questions=questions, user_answers=user_answers)


@student_bp.route('/quizzes/<int:quiz_id>/delete', methods=['POST'])
@login_required
def delete_quiz(quiz_id):
    quiz = Quiz.query.filter_by(id=quiz_id, user_id=current_user.id).first_or_404()
    db.session.delete(quiz)
    db.session.commit()
    flash('Quiz deleted.', 'info')
    return redirect(url_for('student.quizzes'))


# ─── Flashcards ────────────────────────────────────────────────────────────────

@student_bp.route('/flashcards')
@login_required
def flashcards():
    sets = current_user.flashcard_sets.order_by(FlashcardSet.created_at.desc()).all()
    return render_template('student/flashcards.html', sets=sets)


@student_bp.route('/flashcards/set/<int:set_id>')
@login_required
def flashcard_set(set_id):
    fset = FlashcardSet.query.filter_by(id=set_id, user_id=current_user.id).first_or_404()
    cards = fset.flashcards.order_by(Flashcard.order_num).all()
    
    session_log = StudySession(user_id=current_user.id, activity_type='flashcard',
                                activity_id=set_id, subject=fset.subject)
    db.session.add(session_log)
    db.session.commit()
    
    return render_template('student/flashcard_study.html', fset=fset, cards=cards)


@student_bp.route('/flashcards/card/<int:card_id>/toggle-mastered', methods=['POST'])
@login_required
def toggle_card_mastered(card_id):
    card = Flashcard.query.join(FlashcardSet).filter(
        Flashcard.id == card_id,
        FlashcardSet.user_id == current_user.id
    ).first_or_404()
    
    card.is_mastered = not card.is_mastered
    card.times_reviewed += 1
    db.session.commit()
    
    return jsonify({'mastered': card.is_mastered, 'times_reviewed': card.times_reviewed})


@student_bp.route('/flashcards/set/<int:set_id>/delete', methods=['POST'])
@login_required
def delete_flashcard_set(set_id):
    fset = FlashcardSet.query.filter_by(id=set_id, user_id=current_user.id).first_or_404()
    db.session.delete(fset)
    db.session.commit()
    flash('Flashcard set deleted.', 'info')
    return redirect(url_for('student.flashcards'))


# ─── Progress / Analytics ──────────────────────────────────────────────────────

@student_bp.route('/progress')
@login_required
def progress():
    # All-time stats
    total_quiz_attempts = current_user.quiz_attempts.count()
    all_attempts = current_user.quiz_attempts.all()
    avg_score = sum(a.score for a in all_attempts) / len(all_attempts) if all_attempts else 0
    
    # Tasks by status
    task_stats = db.session.query(Task.status, func.count(Task.id)).filter_by(
        user_id=current_user.id
    ).group_by(Task.status).all()
    task_data = {status: count for status, count in task_stats}
    
    # Subjects breakdown
    subject_notes = db.session.query(Note.subject, func.count(Note.id)).filter_by(
        user_id=current_user.id
    ).group_by(Note.subject).all()
    
    # Weekly activity (last 7 days)
    week_ago = datetime.utcnow() - timedelta(days=7)
    weekly_sessions = current_user.study_sessions.filter(
        StudySession.created_at >= week_ago
    ).all()
    
    daily_counts = {}
    for i in range(7):
        day = (datetime.utcnow() - timedelta(days=i)).strftime('%Y-%m-%d')
        daily_counts[day] = 0
    
    for s in weekly_sessions:
        day = s.created_at.strftime('%Y-%m-%d')
        if day in daily_counts:
            daily_counts[day] += 1
    
    # Quiz score history
    quiz_history = current_user.quiz_attempts.order_by(
        QuizAttempt.completed_at.asc()
    ).limit(20).all()
    
    # Flashcard progress
    total_cards = 0
    mastered_cards = 0
    for fset in current_user.flashcard_sets.all():
        cards = fset.flashcards.all()
        total_cards += len(cards)
        mastered_cards += sum(1 for c in cards if c.is_mastered)
    
    return render_template('student/progress.html',
                           total_quiz_attempts=total_quiz_attempts,
                           avg_score=round(avg_score, 1),
                           task_data=task_data,
                           subject_notes=subject_notes,
                           daily_counts=daily_counts,
                           quiz_history=quiz_history,
                           total_cards=total_cards,
                           mastered_cards=mastered_cards)


# ─── Chat ──────────────────────────────────────────────────────────────────────

@student_bp.route('/chat')
@login_required
def chat():
    docs = current_user.documents.filter_by(is_processed=True).order_by(Document.created_at.desc()).all()
    recent_messages = current_user.chat_messages.order_by(
        ChatMessage.created_at.desc()
    ).limit(50).all()
    recent_messages = list(reversed(recent_messages))
    
    return render_template('student/chat.html', 
                           docs=docs, 
                           messages=recent_messages)


# ─── Profile / Settings ────────────────────────────────────────────────────────

@student_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        action = request.form.get('action', 'update_profile')
        
        if action == 'update_profile':
            current_user.full_name = request.form.get('full_name', '').strip() or current_user.full_name
            current_user.bio = request.form.get('bio', '').strip()
            current_user.preferred_subject = request.form.get('preferred_subject', '').strip()
            
            try:
                current_user.study_goal_hours = float(request.form.get('study_goal_hours', 2.0))
            except ValueError:
                pass
            
            avatar_color = request.form.get('avatar_color', '')
            if avatar_color and avatar_color.startswith('#'):
                current_user.avatar_color = avatar_color
            
            db.session.commit()
            flash('Profile updated successfully!', 'success')
        
        elif action == 'change_password':
            from routes.auth import validate_password
            current_password = request.form.get('current_password', '')
            new_password = request.form.get('new_password', '')
            confirm_password = request.form.get('confirm_password', '')
            
            if not current_user.check_password(current_password):
                flash('Current password is incorrect.', 'error')
            elif new_password != confirm_password:
                flash('New passwords do not match.', 'error')
            else:
                errors = validate_password(new_password)
                if errors:
                    for e in errors:
                        flash(e, 'error')
                else:
                    current_user.set_password(new_password)
                    db.session.commit()
                    flash('Password changed successfully!', 'success')
        
        return redirect(url_for('student.profile'))
    
    return render_template('student/profile.html')
