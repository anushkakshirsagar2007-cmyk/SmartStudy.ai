import os
import threading
import pytest

playwright_api = pytest.importorskip('playwright.sync_api', reason='Install Playwright to run browser tests.')
from playwright.sync_api import Error as PlaywrightError, sync_playwright
from werkzeug.serving import make_server
from extensions import create_app, db
from models import User, Quiz, Question, FlashcardSet, Flashcard, Subscription, Task


@pytest.fixture
def running_app(tmp_path, monkeypatch):
    database_path = (tmp_path / 'e2e.sqlite').as_posix()
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{database_path}')
    monkeypatch.setenv('SECRET_KEY', 'playwright-e2e-test-key')
    monkeypatch.setenv('OPENAI_API_KEY', '')
    monkeypatch.setenv('UPI_ID', 'e2e-test@upi')

    app = create_app()
    app.config['UPLOAD_FOLDER'] = str(tmp_path / 'uploads')
    app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024
    with app.app_context():
        db.create_all()
        admin = User(full_name='Browser Admin', username='browseradmin',
                     email='browser-admin@example.test', role='admin')
        admin.set_password('BrowserAdmin1!')
        db.session.add(admin)
        db.session.commit()

    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', app
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_student_workflows_manual_payment_and_admin_approval(running_app):
    base_url, app = running_app
    console_errors = []
    page_errors = []
    failed_posts = []
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as error:
            pytest.skip(f'Install the Playwright Chromium browser to run E2E tests: {error}')

        page = browser.new_page()
        page.on('console', lambda message: console_errors.append(message.text) if message.type == 'error' else None)
        page.on('pageerror', lambda error: page_errors.append(str(error)))
        page.on(
            'response',
            lambda response: failed_posts.append(
                f'{response.request.method} {response.url}: {response.status}'
            ) if response.request.method == 'POST' and response.status >= 400 else None
        )
        page.route('https://fonts.googleapis.com/**', lambda route: route.fulfill(status=200, content_type='text/css', body=''))
        page.route('https://fonts.gstatic.com/**', lambda route: route.fulfill(status=200, body=b''))

        page.goto(f'{base_url}/auth/register')
        page.locator('#full_name').fill('Browser Student')
        page.locator('#username').fill('browserstudent')
        page.locator('#email').fill('browser-student@example.test')
        page.locator('#password').fill('BrowserPass1!')
        page.locator('#confirm_password').fill('BrowserPass1!')
        page.locator('input[name="agree_terms"]').check()
        page.locator('#submit-btn').click()
        page.wait_for_url('**/auth/login')

        page.locator('#email').fill('browser-student@example.test')
        page.locator('#login-password').fill('BrowserPass1!')
        page.locator('#submit-btn').click()
        page.wait_for_url('**/student/dashboard')
        assert page.get_by_text('Your email is not verified.').count() == 1
        page.get_by_role('button', name='Resend verification').click()
        page.wait_for_load_state('networkidle')

        with app.app_context():
            student = User.query.filter_by(email='browser-student@example.test').one()
            quiz = Quiz(user_id=student.id, title='Browser Quiz', subject='Biology', difficulty='easy')
            db.session.add(quiz)
            db.session.flush()
            db.session.add(Question(
                quiz_id=quiz.id, question_text='Where does glycolysis occur?',
                option_a='Cytoplasm', option_b='Nucleus', option_c='Ribosome', option_d='Golgi',
                correct_answer='A', explanation='Glycolysis occurs in the cytoplasm.'
            ))
            deck = FlashcardSet(user_id=student.id, title='Browser Biology Cards', subject='Biology')
            db.session.add(deck)
            db.session.flush()
            db.session.add(Flashcard(set_id=deck.id, front='What is ATP?', back='A cellular energy carrier.'))
            db.session.commit()

        page.locator('#sidebar a[href="/student/workspace"]').click()
        page.get_by_role('button', name='⬆ Upload Document').click()
        page.locator('#file-input').set_input_files({
            'name': 'biology.txt',
            'mimeType': 'text/plain',
            'buffer': b'Cellular respiration converts glucose into energy. Glycolysis occurs in the cytoplasm and produces pyruvate, ATP, and NADH. Mitochondria produce most ATP through oxidative phosphorylation.'
        })
        page.locator('#subject').select_option(label='Biology')
        page.locator('#upload-form button[type="submit"]').click()
        page.wait_for_url('**/student/workspace/document/**')
        assert page.get_by_text('biology.txt').count() > 0

        page.locator('#sidebar a[href="/student/notes"]').click()
        page.get_by_role('link', name='+ New Note').click()
        page.locator('#note-title').fill('Browser Notes')
        page.locator('#note-content').fill('Review cellular respiration and ATP production.')
        page.locator('#note-form button[type="submit"]').click()
        page.wait_for_url('**/student/notes')
        assert page.get_by_text('Browser Notes').count() > 0

        page.locator('#sidebar a[href="/student/planner"]').click()
        page.get_by_role('button', name='➕ Add Task').click()
        page.locator('#task_title').fill('Review biology chapter')
        page.locator('#task_subject').fill('Biology')
        page.locator('#addTaskModal button[type="submit"]').click()
        page.wait_for_load_state('networkidle')
        with app.app_context():
            task = Task.query.filter_by(title='Review biology chapter').one()
            saved_task_count = Task.query.filter_by(title='Review biology chapter').count()
            saved_task_status = task.status
        assert saved_task_count == 1, page.locator('body').inner_text()
        assert page.get_by_text('Review biology chapter').count() > 0, (
            f'Saved task status={saved_task_status}; page text={page.locator("body").inner_text()}'
        )

        page.locator('#sidebar a[href="/student/quizzes"]').click()
        page.get_by_role('link', name='Take Quiz 🚀').click()
        page.locator('input[type="radio"]').first.check()
        page.get_by_role('button', name='Submit Quiz 🏁').click()
        page.wait_for_url('**/student/quizzes/result/**')
        assert page.get_by_text('100.0% Score').count() > 0

        page.locator('#sidebar a[href="/student/flashcards"]').click()
        page.get_by_role('link', name='Study Deck (1) 🎴').click()
        page.locator('#flipCardBox').click()
        assert page.locator('#cardInner').evaluate("(el) => el.classList.contains('flipped')")

        page.locator('#sidebar a[href="/payment/pricing"]').click()
        page.locator('#btn-monthly').click()
        page.locator('#upi-modal').wait_for(state='visible')
        page.locator('#upi-utr-input').fill('123456789012')
        hit_target = page.evaluate("""() => {
          const button = document.getElementById('btn-upi-confirm');
          const rect = button.getBoundingClientRect();
          const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
          return {rect: {top: rect.top, bottom: rect.bottom}, viewport: window.innerHeight,
                  hit: hit && hit.outerHTML};
        }""")
        assert hit_target['hit'] and 'btn-upi-confirm' in hit_target['hit'], hit_target
        page.on('dialog', lambda dialog: dialog.accept())
        page.locator('#btn-upi-confirm').click()
        page.wait_for_load_state('networkidle')

        page.locator('form[action="/auth/logout"] button[type="submit"]').click()
        page.wait_for_url(f'{base_url}/')
        page.get_by_role('link', name='Sign In', exact=True).click()
        page.locator('#email').fill('browser-admin@example.test')
        page.locator('#login-password').fill('BrowserAdmin1!')
        page.locator('#submit-btn').click()
        page.wait_for_url('**/admin/dashboard')
        page.locator('#sidebar a[href="/admin/payments"]').click()
        page.get_by_role('button', name='Approve').click()
        page.wait_for_load_state('networkidle')

        with app.app_context():
            student = User.query.filter_by(email='browser-student@example.test').one()
            subscription = Subscription.query.filter_by(user_id=student.id).one()
            assert subscription.payment_status == 'paid'
            assert student.subscription_plan == 'monthly'

        page.locator('form[action="/auth/logout"] button[type="submit"]').click()
        page.wait_for_url(f'{base_url}/')
        browser.close()

    assert not failed_posts, f'Form/API submissions failed: {failed_posts}'
    assert not page_errors, f'JavaScript page errors: {page_errors}'
    assert not console_errors, f'Browser console errors: {console_errors}'
