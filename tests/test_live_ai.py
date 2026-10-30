import os
import re
import pytest
from extensions import create_app, db
from models import Document, User


pytestmark = pytest.mark.skipif(
    not os.environ.get('OPENAI_API_KEY'),
    reason='Set OPENAI_API_KEY to run live provider integration checks.'
)


def test_live_ai_endpoints_return_expected_structures(tmp_path, monkeypatch):
    database_path = (tmp_path / 'live-ai.sqlite').as_posix()
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{database_path}')
    monkeypatch.setenv('SECRET_KEY', 'live-integration-test-key')
    monkeypatch.setenv('OPENAI_API_KEY', os.environ['OPENAI_API_KEY'])
    monkeypatch.setenv('OPENAI_MODEL', os.environ.get('OPENAI_MODEL', 'gpt-4o-mini'))

    app = create_app()
    app.config['TESTING'] = True
    app.config['UPLOAD_FOLDER'] = str(tmp_path / 'uploads')
    with app.app_context():
        db.create_all()
        user = User(full_name='Live Integration', username='liveintegration',
                    email='live-integration@example.test', is_verified=True)
        user.set_password('LiveTestPass1!')
        db.session.add(user)
        db.session.flush()
        document = Document(
            user_id=user.id,
            original_name='cell-biology.txt',
            stored_name='unused.txt',
            file_type='txt',
            content_text=(
                'Cellular respiration converts glucose into usable energy. Glycolysis occurs in the cytoplasm '
                'and produces pyruvate, ATP, and NADH. In eukaryotic cells, pyruvate enters mitochondria and '
                'is further processed in the Krebs cycle. The electron transport chain uses electrons carried '
                'by NADH and FADH2 to establish a proton gradient. ATP synthase uses that gradient to produce '
                'ATP. Oxygen is the final electron acceptor and combines with electrons and protons to form water. '
            ) * 3,
            subject='Biology',
            is_processed=True
        )
        db.session.add(document)
        db.session.commit()
        document_id = document.id

    client = app.test_client()
    login_page = client.get('/auth/login').data.decode()
    csrf_token = re.search(r'name="csrf-token" content="([^"]+)"', login_page).group(1)
    login = client.post('/auth/login', data={
        'email': 'live-integration@example.test',
        'password': 'LiveTestPass1!',
        'csrf_token': csrf_token
    })
    assert login.status_code == 302
    csrf_token = re.search(
        r'name="csrf-token" content="([^"]+)"',
        client.get('/student/dashboard').data.decode()
    ).group(1)
    headers = {'X-CSRFToken': csrf_token}

    def call(path, payload):
        response = client.post(path, json=payload, headers=headers)
        data = response.get_json()
        assert response.status_code == 200, (
            f'{path} failed with {response.status_code}: '
            f'{(data or {}).get("error", response.get_data(as_text=True))}'
        )
        return data

    notes = call('/api/ai/generate-notes', {'document_id': document_id})
    assert notes.get('success') is True and isinstance(notes.get('note_id'), int)

    quiz = call('/api/ai/generate-quiz', {
        'document_id': document_id, 'num_questions': 2, 'difficulty': 'easy'
    })
    assert quiz.get('success') is True and isinstance(quiz.get('quiz_id'), int)

    flashcards = call('/api/ai/generate-flashcards', {
        'document_id': document_id, 'num_cards': 2
    })
    assert flashcards.get('success') is True and isinstance(flashcards.get('set_id'), int)

    answer = call('/api/ai/ask-document', {
        'document_id': document_id, 'question': 'Where does glycolysis occur?'
    })
    assert isinstance(answer.get('answer'), str) and answer['answer'].strip()

    chat = call('/api/ai/chat', {'message': 'Explain ATP synthase in one sentence.'})
    assert isinstance(chat.get('response'), str) and chat['response'].strip()

    with app.app_context():
        from models import FlashcardSet, Note, Quiz
        assert Note.query.filter_by(id=notes['note_id']).one().content.strip()
        assert Quiz.query.filter_by(id=quiz['quiz_id']).one().questions.count() > 0
        assert FlashcardSet.query.filter_by(id=flashcards['set_id']).one().flashcards.count() > 0
