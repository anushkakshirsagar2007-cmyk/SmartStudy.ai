import json
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
from models import (Note, Quiz, Question, FlashcardSet, Flashcard, ChatMessage,
                    Document, StudySession, Task)
from extensions import db

api_bp = Blueprint('api', __name__)


def get_ai_client():
    """Build an OpenRouter client using its OpenAI-compatible API."""
    import os
    from dotenv import load_dotenv

    load_dotenv(override=False)

    api_key = (
        current_app.config.get('OPENROUTER_API_KEY')
        or os.environ.get('OPENROUTER_API_KEY', '')
    ).strip()
    if not api_key:
        return None

    from openai import OpenAI
    return OpenAI(
        api_key=api_key,
        base_url='https://openrouter.ai/api/v1',
        timeout=current_app.config.get('OPENROUTER_TIMEOUT', 30),
        default_headers={
            'HTTP-Referer': 'https://smartstudy.ai',
            'X-OpenRouter-Title': 'SmartStudy.ai'
        }
    )


def ai_error_response(exc, operation='AI request'):
    """Translate common provider failures into actionable, non-secret messages."""
    message = str(exc)
    lowered = message.lower()
    status = getattr(exc, 'status_code', None)
    code = str(getattr(exc, 'code', '') or '').lower()
    if status == 401 or any(term in lowered for term in ('invalid api key', 'incorrect api key', 'authentication_error')):
        return jsonify({'error': 'OpenRouter rejected the API key. Check OPENROUTER_API_KEY and try again.'}), 401
    if status == 429 or 'rate_limit' in code or 'quota' in lowered or 'rate limit' in lowered:
        return jsonify({'error': 'OpenRouter rate limit or quota reached. Check your account quota or try again later.'}), 429
    if status == 404 or 'model_not_found' in code or 'model not found' in lowered or 'does not exist' in lowered:
        return jsonify({'error': 'The model is unavailable on OpenRouter. Check OPENROUTER_MODEL and try again.'}), 400
    if 'timeout' in exc.__class__.__name__.lower() or 'timeout' in lowered or 'timed out' in lowered:
        return jsonify({'error': 'The AI provider timed out. Please try again.'}), 504
    current_app.logger.warning('%s failed: %s', operation, message)
    return jsonify({'error': f'{operation} failed. Please try again.'}), 502


def require_ai_client():
    """Return the OpenRouter client, model, or a Flask error response."""
    client = get_ai_client()
    if not client:
        return None, None, (jsonify({
            'error': 'AI is not configured. Add OPENROUTER_API_KEY in the server environment and redeploy.'
        }), 503)

    import os
    model = (
        current_app.config.get('OPENROUTER_MODEL')
        or os.environ.get('OPENROUTER_MODEL', 'openrouter/free')
    ).strip() or 'openrouter/free'
    return client, model, None


def generate_vision_response(client, model, prompt, images, max_tokens):
    """Run multimodal analysis through OpenRouter."""
    import base64
    content = [{'type': 'text', 'text': prompt}]
    content.extend({
        'type': 'image_url',
        'image_url': {'url': f'data:{mime_type};base64,{base64.b64encode(data).decode("ascii")}'}
    } for mime_type, data in images)
    response = client.chat.completions.create(
        model=model,
        messages=[{'role': 'user', 'content': content}],
        max_tokens=max_tokens
    )
    return response.choices[0].message.content or ''


def truncate_text(text, max_chars=8000):
    """Truncate text to fit within token limits."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + '\n...[content truncated for processing]'


def split_text_into_chunks(text, max_chars=10000):
    """Split text without dropping content, preferring paragraph boundaries."""
    chunks = []
    current = ''
    for paragraph in text.splitlines(keepends=True):
        while len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ''
            chunks.append(paragraph[:max_chars])
            paragraph = paragraph[max_chars:]
        if current and len(current) + len(paragraph) > max_chars:
            chunks.append(current)
            current = ''
        current += paragraph
    if current:
        chunks.append(current)
    return chunks


def generate_document_material(client, model, title, subject, content, material_type):
    """Generate notes or a summary from every source-text chunk, then consolidate."""
    chunks = split_text_into_chunks(content)
    if not chunks:
        raise ValueError('The document has no extractable text to process.')

    is_notes = material_type == 'notes'
    section_instruction = (
        'Create detailed, source-faithful study notes for this section. Preserve definitions, '
        'important facts, equations, examples, and page references.'
        if is_notes else
        'Create a detailed, source-faithful summary of this section. Preserve key concepts, '
        'definitions, facts, equations, examples, and page references.'
    )
    final_instruction = (
        'Create comprehensive, organized study notes with Markdown headings, definitions, '
        'important facts, equations, examples, and key takeaways.'
        if is_notes else
        'Create a clear student-friendly summary with Overview, Key Topics, Important Details '
        '(including definitions, formulas, and examples), and Main Takeaways.'
    )
    grounding = (
        'Use only facts present in the supplied document text. Do not invent missing information. '
        'If the source is unclear, say so.'
    )

    def request_material(instruction, source, max_tokens):
        response = client.chat.completions.create(
            model=model,
            messages=[
                {'role': 'system', 'content': f'{instruction}\n{grounding}'},
                {
                    'role': 'user',
                    'content': f'Subject: {subject}\nDocument: {title}\n\n{source}'
                }
            ],
            max_tokens=max_tokens,
            temperature=0.2
        )
        result = response.choices[0].message.content
        if not result or not result.strip():
            raise RuntimeError('The AI model returned an empty document analysis.')
        return result.strip()

    if len(chunks) == 1:
        return request_material(final_instruction, chunks[0], 2500)

    summaries = [
        request_material(
            f'{section_instruction} This is section {index} of {len(chunks)}.',
            chunk,
            1400
        )
        for index, chunk in enumerate(chunks, start=1)
    ]

    while len(summaries) > 4:
        summaries = [
            request_material(
                'Consolidate these section analyses without losing source-specific facts, '
                'definitions, equations, examples, or page references. Remove only repetition.',
                '\n\n'.join(summaries[index:index + 4]),
                1800
            )
            for index in range(0, len(summaries), 4)
        ]

    return request_material(
        final_instruction,
        '\n\n'.join(summaries),
        3000
    )


def parse_json_response(content):
    """Robustly parse JSON from AI response."""
    content = content.strip()
    # Remove markdown code fences
    if content.startswith('```'):
        lines = content.split('\n')
        # Remove first line (```json or ```)
        lines = lines[1:]
        # Remove last line if it's ```)
        if lines and lines[-1].strip() == '```':
            lines = lines[:-1]
        content = '\n'.join(lines)
    # Try direct parse
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    # Try to find JSON array in the content
    start = content.find('[')
    end = content.rfind(']')
    if start != -1 and end != -1:
        try:
            return json.loads(content[start:end+1])
        except json.JSONDecodeError:
            pass
    # Try to find JSON object
    start = content.find('{')
    end = content.rfind('}')
    if start != -1 and end != -1:
        try:
            return json.loads(content[start:end+1])
        except json.JSONDecodeError:
            pass
    return None


# ─── AI: Generate Notes ─────────────────────────────────────────────────────────

@api_bp.route('/ai/generate-notes', methods=['POST'])
@login_required
def generate_notes():
    data = request.get_json()
    doc_id = data.get('document_id')
    
    doc = Document.query.filter_by(id=doc_id, user_id=current_user.id).first()
    if not doc:
        return jsonify({'error': 'Document not found'}), 404

    if not doc.is_processed:
        return jsonify({'error': 'Document processing is incomplete. Re-upload it or resolve the extraction warning before generating study material.'}), 400
    
    if not doc.content_text or len(doc.content_text.strip()) < 50:
        return jsonify({'error': 'Document has insufficient extractable text. Please upload a document with readable content.'}), 400
    
    client, model, error = require_ai_client()
    if error:
        return error
    
    try:
        notes_content = generate_document_material(
            client, model, doc.original_name, doc.subject, doc.content_text, 'notes'
        )
        
        note = Note(
            user_id=current_user.id,
            title=f'AI Notes: {doc.original_name}',
            content=notes_content,
            subject=doc.subject,
            is_ai_generated=True,
            source_document_id=doc.id,
            color='#7C3AED'
        )
        db.session.add(note)
        
        session_log = StudySession(user_id=current_user.id, activity_type='notes', subject=doc.subject)
        db.session.add(session_log)
        db.session.commit()
        
        return jsonify({'success': True, 'note_id': note.id, 'message': 'Notes generated successfully!'})
    
    except Exception as e:
        return ai_error_response(e, 'Notes generation')


# ─── AI: Generate Summary ───────────────────────────────────────────────────────

@api_bp.route('/ai/generate-summary', methods=['POST'])
@login_required
def generate_summary():
    data = request.get_json()
    doc_id = data.get('document_id')
    
    doc = Document.query.filter_by(id=doc_id, user_id=current_user.id).first()
    if not doc:
        return jsonify({'error': 'Document not found'}), 404

    if not doc.is_processed:
        return jsonify({'error': 'Document processing is incomplete. Re-upload it or resolve the extraction warning before generating a summary.'}), 400
    
    if not doc.content_text or len(doc.content_text.strip()) < 50:
        return jsonify({'error': 'Document has insufficient text for summarization.'}), 400
    
    client, model, error = require_ai_client()
    if error:
        return error
    
    try:
        summary = generate_document_material(
            client, model, doc.original_name, doc.subject, doc.content_text, 'summary'
        )
        doc.summary = summary
        db.session.commit()
        
        return jsonify({'success': True, 'summary': summary})
    
    except Exception as e:
        return ai_error_response(e, 'Summary generation')


# ─── AI: Generate Quiz ──────────────────────────────────────────────────────────

@api_bp.route('/ai/generate-quiz', methods=['POST'])
@login_required
def generate_quiz():
    data = request.get_json()
    doc_id = data.get('document_id')
    num_questions = min(int(data.get('num_questions', 5)), 15)
    difficulty = data.get('difficulty', 'medium')
    
    doc = Document.query.filter_by(id=doc_id, user_id=current_user.id).first()
    if not doc:
        return jsonify({'error': 'Document not found'}), 404
    
    if not doc.content_text or len(doc.content_text.strip()) < 100:
        return jsonify({'error': 'Document needs more text content to generate meaningful quiz questions.'}), 400
    
    client, model, error = require_ai_client()
    if error:
        return error
    
    try:
        text = truncate_text(doc.content_text, 6000)
        
        difficulty_desc = {
            'easy': 'Basic recall and comprehension questions. Test fundamental understanding.',
            'medium': 'Application and analysis questions. Require understanding of concepts and their relationships.',
            'hard': 'Critical thinking and synthesis questions. Require deep understanding, comparison, and evaluation.'
        }
        
        prompt = f"""Create exactly {num_questions} multiple-choice questions at {difficulty} difficulty based STRICTLY on this document content. Do NOT use any outside knowledge.

Difficulty level: {difficulty_desc.get(difficulty, difficulty_desc['medium'])}

Rules:
- Every question MUST be answerable from the document content
- Each question should test a different concept/topic from the document
- All 4 options should be plausible (no obviously wrong answers)
- Include clear, educational explanations for the correct answer
- Ensure correct_answer is exactly A, B, C, or D

Return ONLY a valid JSON array with this exact format (no extra text):
[
  {{
    "question": "Clear, specific question text?",
    "option_a": "First option",
    "option_b": "Second option", 
    "option_c": "Third option",
    "option_d": "Fourth option",
    "correct_answer": "A",
    "explanation": "Clear explanation referencing the document content"
  }}
]

Document subject: {doc.subject}
Document content:
{text}"""
        
        response = client.chat.completions.create(
            model=model,
            messages=[
                {'role': 'system', 'content': 'You are an expert academic quiz creator. Generate quiz questions strictly from the provided document. Always respond with valid JSON only, no markdown fences or extra text.'},
                {'role': 'user', 'content': prompt}
            ],
            max_tokens=3000,
            temperature=0.4
        )
        
        content = response.choices[0].message.content
        questions_data = parse_json_response(content)
        
        if not questions_data or not isinstance(questions_data, list):
            return jsonify({'error': 'Failed to parse AI response. Please try again.'}), 500
        
        quiz = Quiz(
            user_id=current_user.id,
            title=f'Quiz: {doc.original_name}',
            description=f'{difficulty.title()} difficulty quiz from {doc.original_name} ({num_questions} questions)',
            subject=doc.subject,
            difficulty=difficulty,
            is_ai_generated=True,
            source_document_id=doc.id
        )
        db.session.add(quiz)
        db.session.flush()
        
        for i, q_data in enumerate(questions_data[:num_questions]):
            correct = q_data.get('correct_answer', 'A').upper().strip()
            if correct not in ('A', 'B', 'C', 'D'):
                correct = 'A'
            
            question = Question(
                quiz_id=quiz.id,
                question_text=q_data.get('question', '').strip(),
                option_a=q_data.get('option_a', '').strip(),
                option_b=q_data.get('option_b', '').strip(),
                option_c=q_data.get('option_c', '').strip(),
                option_d=q_data.get('option_d', '').strip(),
                correct_answer=correct,
                explanation=q_data.get('explanation', '').strip(),
                order_num=i
            )
            db.session.add(question)
        
        session_log = StudySession(user_id=current_user.id, activity_type='quiz', 
                                    activity_id=quiz.id, subject=doc.subject)
        db.session.add(session_log)
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'quiz_id': quiz.id,
            'message': f'Quiz generated with {len(questions_data[:num_questions])} questions!'
        })
    
    except Exception as e:
        db.session.rollback()
        return ai_error_response(e, 'Quiz generation')


# ─── AI: Generate Flashcards ────────────────────────────────────────────────────

@api_bp.route('/ai/generate-flashcards', methods=['POST'])
@login_required
def generate_flashcards():
    data = request.get_json()
    doc_id = data.get('document_id')
    num_cards = min(int(data.get('num_cards', 10)), 25)
    
    doc = Document.query.filter_by(id=doc_id, user_id=current_user.id).first()
    if not doc:
        return jsonify({'error': 'Document not found'}), 404
    
    if not doc.content_text or len(doc.content_text.strip()) < 100:
        return jsonify({'error': 'Document needs more text content to generate flashcards.'}), 400
    
    client, model, error = require_ai_client()
    if error:
        return error
    
    try:
        text = truncate_text(doc.content_text, 5000)
        
        prompt = f"""Create exactly {num_cards} study flashcards from this document for effective learning.

Rules:
- Each flashcard should cover a distinct key concept, definition, formula, or important fact from the document
- Front: Clear, specific question or term (not too vague)
- Back: Concise, accurate answer or definition from the document
- Cover the most important topics first
- Vary the types: definitions, explanations, comparisons, applications

Return ONLY a valid JSON array:
[
  {{"front": "What is [specific concept]?", "back": "Concise, accurate answer from the document"}}
]

Document subject: {doc.subject}
Document content:
{text}"""
        
        response = client.chat.completions.create(
            model=model,
            messages=[
                {'role': 'system', 'content': 'You create precise study flashcards from academic documents. Respond with valid JSON only.'},
                {'role': 'user', 'content': prompt}
            ],
            max_tokens=2000,
            temperature=0.4
        )
        
        content = response.choices[0].message.content
        cards_data = parse_json_response(content)
        
        if not cards_data or not isinstance(cards_data, list):
            return jsonify({'error': 'Failed to parse AI response. Please try again.'}), 500
        
        fset = FlashcardSet(
            user_id=current_user.id,
            title=f'Flashcards: {doc.original_name}',
            description=f'AI-generated flashcards from {doc.original_name}',
            subject=doc.subject,
            is_ai_generated=True,
            source_document_id=doc.id
        )
        db.session.add(fset)
        db.session.flush()
        
        for i, card_data in enumerate(cards_data[:num_cards]):
            front = card_data.get('front', '').strip()
            back = card_data.get('back', '').strip()
            if front and back:
                card = Flashcard(
                    set_id=fset.id,
                    front=front,
                    back=back,
                    order_num=i
                )
                db.session.add(card)
        
        session_log = StudySession(user_id=current_user.id, activity_type='flashcard',
                                    activity_id=fset.id, subject=doc.subject)
        db.session.add(session_log)
        db.session.commit()
        
        return jsonify({'success': True, 'set_id': fset.id, 'message': f'Flashcards generated ({len(cards_data[:num_cards])} cards)!'})
    
    except Exception as e:
        db.session.rollback()
        return ai_error_response(e, 'Flashcard generation')


# ─── AI: Chat ──────────────────────────────────────────────────────────────────

@api_bp.route('/ai/chat', methods=['POST'])
@login_required
def ai_chat():
    data = request.get_json()
    user_message = data.get('message', '').strip()
    doc_id = data.get('document_id')
    session_id = data.get('session_id', 'default')
    
    if not user_message:
        return jsonify({'error': 'Message cannot be empty'}), 400
    
    # Save user message
    user_msg = ChatMessage(
        user_id=current_user.id,
        role='user',
        content=user_message,
        document_id=doc_id,
        session_id=session_id
    )
    db.session.add(user_msg)
    
    client, model, error = require_ai_client()
    if error:
        db.session.commit()  # Save user message
        return error
    
    try:
        # Build conversation history
        history = current_user.chat_messages.filter_by(session_id=session_id).order_by(
            ChatMessage.created_at.asc()
        ).limit(20).all()
        
        system_prompt = '''You are SmartStudy AI, an expert academic tutor and study assistant. You help students understand their study materials deeply.

Guidelines:
- Provide clear, accurate, educational answers
- Use examples and analogies to explain complex concepts
- Reference the document content when available
- Be encouraging but honest — if you don't know, say so
- Format responses with markdown (headers, bold, bullet points) for readability
- For math/science, show step-by-step solutions when relevant'''
        
        messages = [{'role': 'system', 'content': system_prompt}]
        
        # Add document context if provided
        if doc_id:
            doc = Document.query.filter_by(id=doc_id, user_id=current_user.id).first()
            if doc and doc.content_text:
                context = truncate_text(doc.content_text, 4000)
                messages[0]['content'] += f'\n\nThe student is studying this document:\n---\nTitle: {doc.original_name}\nSubject: {doc.subject}\nContent:\n{context}\n---\nAnswer questions based on this document when relevant. Cite specific parts of the document.'
        
        for msg in history[-10:]:  # Last 10 messages for context
            messages.append({'role': msg.role, 'content': msg.content})
        
        messages.append({'role': 'user', 'content': user_message})
        
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=1000,
            temperature=0.5
        )
        
        ai_response = response.choices[0].message.content
        
        ai_msg = ChatMessage(
            user_id=current_user.id,
            role='assistant',
            content=ai_response,
            document_id=doc_id,
            session_id=session_id
        )
        db.session.add(ai_msg)
        db.session.commit()
        
        return jsonify({'response': ai_response})
    
    except Exception as e:
        db.session.commit()
        return ai_error_response(e, 'Chat response')


# ─── AI: Ask Document ──────────────────────────────────────────────────────────

@api_bp.route('/ai/ask-document', methods=['POST'])
@login_required
def ask_document():
    data = request.get_json()
    question = data.get('question', '').strip()
    doc_id = data.get('document_id')
    
    if not question:
        return jsonify({'error': 'Question cannot be empty'}), 400
    
    doc = Document.query.filter_by(id=doc_id, user_id=current_user.id).first()
    if not doc:
        return jsonify({'error': 'Document not found'}), 404
    
    if not doc.content_text or len(doc.content_text.strip()) < 50:
        return jsonify({'error': 'Document has insufficient text to answer questions.'}), 400
    
    client, model, error = require_ai_client()
    if error:
        return error
    
    try:
        text = truncate_text(doc.content_text, 6000)
        
        response = client.chat.completions.create(
            model=model,
            messages=[
                {'role': 'system', 'content': '''Answer the student's question based STRICTLY on the provided document content. 
- If the answer is in the document, provide a clear, detailed answer with references to specific sections
- If the answer is NOT in the document, clearly say "This information is not covered in your document" 
- Be accurate — do not make up information
- Use markdown formatting for clarity'''},
                {'role': 'user', 'content': f'Document ({doc.subject}): {doc.original_name}\n\nContent:\n{text}\n\nStudent Question: {question}'}
            ],
            max_tokens=800,
            temperature=0.2
        )
        
        return jsonify({'answer': response.choices[0].message.content})
    
    except Exception as e:
        return ai_error_response(e, 'Document Q&A')


# ─── Task API ──────────────────────────────────────────────────────────────────

@api_bp.route('/tasks/<int:task_id>', methods=['PUT'])
@login_required
def update_task(task_id):
    task = Task.query.filter_by(id=task_id, user_id=current_user.id).first_or_404()
    data = request.get_json()
    
    if 'status' in data:
        task.status = data['status']
        if data['status'] == 'completed':
            task.completed_at = datetime.utcnow()
    if 'priority' in data:
        task.priority = data['priority']
    
    db.session.commit()
    return jsonify({'success': True, 'task': task.to_dict()})


# ─── Flashcard API ─────────────────────────────────────────────────────────────

@api_bp.route('/flashcard-sets/<int:set_id>/cards', methods=['GET'])
@login_required
def get_flashcards(set_id):
    fset = FlashcardSet.query.filter_by(id=set_id, user_id=current_user.id).first_or_404()
    cards = fset.flashcards.order_by(Flashcard.order_num).all()
    return jsonify({'cards': [c.to_dict() for c in cards]})


# ─── Stats API ─────────────────────────────────────────────────────────────────

@api_bp.route('/stats/weekly-activity')
@login_required
def weekly_activity():
    from datetime import timedelta
    week_ago = datetime.utcnow() - timedelta(days=7)
    sessions = current_user.study_sessions.filter(
        StudySession.created_at >= week_ago
    ).all()
    
    daily = {}
    for i in range(7):
        day = (datetime.utcnow() - timedelta(days=i)).strftime('%Y-%m-%d')
        daily[day] = 0
    
    for s in sessions:
        day = s.created_at.strftime('%Y-%m-%d')
        if day in daily:
            daily[day] += 1
    
    return jsonify({'daily_activity': daily})
