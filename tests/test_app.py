import hashlib, io, os, re, tempfile
from sqlalchemy import create_engine, inspect
_tmp = tempfile.mkdtemp()
os.environ.update(SECRET_KEY="test-secret", DATABASE_URL=f"sqlite:///{_tmp}/t.db", FLASK_DEBUG="False",
                  OPENAI_API_KEY="", UPI_ID="student-test@upi", RAZORPAY_KEY_ID="ignored-key",
                  RAZORPAY_KEY_SECRET="ignored-secret", ADMIN_EMAIL="admin@t.co", ADMIN_PASSWORD="AdminPass@123")
import pytest
from extensions import db
from app import app
from models import User, Subscription, SubscriptionActivation, Document
import init_db
app.config["UPLOAD_FOLDER"] = os.path.join(_tmp, "uploads")
with app.app_context():
    db.create_all()
    a = User(full_name="Admin", username="admin", email="admin@t.co", role="admin"); a.set_password("AdminPass@123"); db.session.add(a); db.session.commit()

PW = "Passw0rd!x"
def token(c, path):
    return re.search(r'name="csrf-token" content="([^"]+)"', c.get(path).data.decode()).group(1)
def post(c, path, data, t): return c.post(path, data={**data, "csrf_token": t})
def js(c, path, body, t, method="post"): return getattr(c, method)(path, json=body, headers={"X-CSRFToken": t})
def student(email, username=None):
    c = app.test_client()
    t = token(c, "/auth/register")
    post(c, "/auth/register", {"full_name": "Test User", "username": username or email.split("@")[0], "email": email, "password": PW, "confirm_password": PW, "agree_terms": "on"}, t)
    t = token(c, "/auth/login"); post(c, "/auth/login", {"email": email, "password": PW}, t)
    return c, token(c, "/student/dashboard")
def admin():
    c = app.test_client(); t = token(c, "/auth/login"); post(c, "/auth/login", {"email": "admin@t.co", "password": "AdminPass@123"}, t)
    return c, token(c, "/admin/payments")
def user(email):
    with app.app_context(): return User.query.filter_by(email=email).first()

def test_csrf_blocks_forms():
    c = app.test_client(); token(c, "/auth/register")
    c.post("/auth/register", data={"full_name": "No Csrf", "username": "nocsrf", "email": "nocsrf@t.co", "password": PW, "confirm_password": PW})
    assert user("nocsrf@t.co") is None

def test_register_login_dashboard():
    c, _ = student("alice1@t.co")
    assert c.get("/student/dashboard").status_code == 200
    assert user("alice1@t.co").consent_at is not None

def test_dashboard_requires_login():
    assert app.test_client().get("/student/dashboard").status_code == 302

def test_upi_payment_needs_admin_approval():
    c, t = student("pay1@t.co")
    oid = js(c, "/payment/create-order", {"plan_type": "monthly"}, t).get_json()["order_id"]
    assert js(c, "/payment/direct-upi-verify", {"order_id": oid, "utr_number": "INSTANT_TEST_PASS", "is_test_mode": True}, t).status_code == 400
    with app.app_context():
        sub = Subscription.query.filter_by(order_id=oid).one()
        assert sub.payment_status == "pending" and not sub.is_active
        assert Subscription.query.filter_by(order_id=oid).count() == 1
    assert c.post("/payment/verify", json={}, headers={"X-CSRFToken": t}).status_code == 404
    assert js(c, "/payment/direct-upi-verify", {"order_id": oid, "utr_number": "111122223333"}, t).status_code == 200
    assert user("pay1@t.co").subscription_plan == "free"
    ac, at = admin()
    with app.app_context(): sid = Subscription.query.filter_by(order_id=oid).first().id
    assert ac.post(f"/admin/payments/{sid}/approve").status_code == 302      # no CSRF token -> not approved
    assert user("pay1@t.co").subscription_plan == "free"
    post(ac, f"/admin/payments/{sid}/approve", {}, at)
    assert user("pay1@t.co").subscription_plan == "monthly"

def test_utr_cannot_be_reused_or_stolen():
    c1, t1 = student("pay2@t.co"); c2, t2 = student("pay3@t.co")
    o1 = js(c1, "/payment/create-order", {"plan_type": "monthly"}, t1).get_json()["order_id"]
    o2 = js(c2, "/payment/create-order", {"plan_type": "monthly"}, t2).get_json()["order_id"]
    assert js(c1, "/payment/direct-upi-verify", {"order_id": o1, "utr_number": "444455556666"}, t1).status_code == 200
    assert js(c2, "/payment/direct-upi-verify", {"order_id": o2, "utr_number": "444455556666"}, t2).status_code == 400
    assert js(c2, "/payment/direct-upi-verify", {"order_id": o1, "utr_number": "777788889999"}, t2).status_code == 404

def test_students_cannot_see_each_others_documents():
    c1, t1 = student("iso1@t.co"); c2, t2 = student("iso2@t.co")
    r = c1.post("/student/workspace/upload", data={"file": (io.BytesIO(b"Mitochondria produce ATP in cells."), "a.txt"), "subject": "Bio", "csrf_token": t1}, content_type="multipart/form-data")
    with app.app_context(): did = Document.query.filter_by(user_id=user("iso1@t.co").id).first().id
    assert c1.get(f"/student/workspace/document/{did}").status_code == 200
    assert c2.get(f"/student/workspace/document/{did}").status_code == 404
    assert js(c2, "/api/ai/generate-notes", {"document_id": did}, t2).status_code in (404, 503)

def test_student_cannot_use_admin_area():
    c, t = student("noadmin@t.co")
    assert c.get("/admin/payments").status_code == 302
    assert c.get("/admin/dashboard").status_code == 302

def test_oversize_upload_rejected():
    c, t = student("big@t.co")
    c.post("/student/workspace/upload", data={"file": (io.BytesIO(b"x" * (11 * 1024 * 1024)), "big.txt"), "subject": "G", "csrf_token": t}, content_type="multipart/form-data")
    with app.app_context(): assert Document.query.filter_by(user_id=user("big@t.co").id).count() == 0

def test_ai_without_key_is_clean_error():
    c, t = student("aitester@t.co"); r = js(c, "/api/ai/chat", {"message": "hi"}, t)
    assert r.status_code == 503 and "API key" in r.get_json()["error"]

def test_login_is_throttled():
    c = app.test_client(); t = token(c, "/auth/login")
    for _ in range(5): post(c, "/auth/login", {"email": "alice1@t.co", "password": "wrong"}, t)
    assert post(c, "/auth/login", {"email": "alice1@t.co", "password": PW}, t).status_code == 429

def test_logout_is_post_only():
    c, t = student("out@t.co")
    assert c.get("/auth/logout").status_code == 405
    post(c, "/auth/logout", {}, t); assert c.get("/student/dashboard").status_code == 302

def test_admin_can_reset_student_password():
    student("reset@t.co"); ac, at = admin(); uid = user("reset@t.co").id
    post(ac, f"/admin/students/{uid}/reset-password", {}, at)
    c = app.test_client(); t = token(c, "/auth/login")
    assert post(c, "/auth/login", {"email": "reset@t.co", "password": PW}, t).status_code == 200   # old password no longer works

def test_seo_files():
    c = app.test_client()
    assert "Disallow: /student/" in c.get("/robots.txt").data.decode()
    assert "<urlset" in c.get("/sitemap.xml").data.decode()
    for p in ("/terms", "/privacy", "/refund", "/"): assert c.get(p).status_code == 200


def test_registration_requires_terms_consent():
    c = app.test_client()
    t = token(c, "/auth/register")
    response = post(c, "/auth/register", {
        "full_name": "No Consent", "username": "noconsent", "email": "noconsent@t.co",
        "password": PW, "confirm_password": PW
    }, t)
    assert b"agree to the Terms and Privacy Policy" in response.data
    assert user("noconsent@t.co") is None


def test_password_reset_is_generic_hashed_expiring_and_single_use(monkeypatch):
    student("passwordreset1@t.co")
    sent = []
    monkeypatch.setattr("routes.auth.send_email", lambda recipient, subject, body: sent.append((recipient, subject, body)))
    c = app.test_client()
    t = token(c, "/auth/forgot-password")
    existing = c.post("/auth/forgot-password", data={"email": "passwordreset1@t.co", "csrf_token": t}, follow_redirects=True)
    existing_text = existing.data.decode()
    t = token(c, "/auth/forgot-password")
    unknown = c.post("/auth/forgot-password", data={"email": "unknown-reset@t.co", "csrf_token": t}, follow_redirects=True)
    assert "If an account matches that email" in existing_text
    assert "If an account matches that email" in unknown.data.decode()
    assert len(sent) == 1

    reset_link = re.search(r'/auth/reset-password/(\S+)', sent[0][2]).group(1)
    raw_hash = hashlib.sha256(reset_link.encode()).hexdigest()
    with app.app_context():
        from models import PasswordResetToken
        reset_record = PasswordResetToken.query.filter_by(token_hash=raw_hash).one()
        assert reset_record.used_at is None
        assert len(reset_record.token_hash) == 64 and reset_link not in reset_record.token_hash

    path = f"/auth/reset-password/{reset_link}"
    t = token(c, path)
    post(c, path, {"password": "NewPassw0rd!", "confirm_password": "NewPassw0rd!"}, t)
    with app.app_context():
        from models import PasswordResetToken
        reset_record = PasswordResetToken.query.filter_by(token_hash=raw_hash).one()
        assert reset_record.used_at is not None
        assert user("passwordreset1@t.co").check_password("NewPassw0rd!")
    assert c.get(path).status_code == 302


def test_email_verification_uses_signed_token():
    c, _ = student("verifyemail1@t.co")
    with app.app_context():
        import routes.auth as auth
        account = user("verifyemail1@t.co")
        signed = auth._token_serializer().dumps({"user_id": account.id, "email": account.email})
        assert not account.is_verified
    response = c.get(f"/auth/verify-email/{signed}")
    assert response.status_code == 302
    with app.app_context():
        account = user("verifyemail1@t.co")
        assert account.is_verified and account.email_verified_at is not None


def test_manual_upi_is_the_only_payment_flow_and_approval_is_idempotent(monkeypatch):
    c, t = student("manualupi1@t.co")
    assert "RAZORPAY_KEY_ID" not in app.config
    assert "RAZORPAY_KEY_SECRET" not in app.config
    created = js(c, "/payment/create-order", {"plan_type": "monthly"}, t)
    assert created.status_code == 200
    order = created.get_json()
    assert order["gateway"] == "upi_manual"
    assert order["upi_id"] == "student-test@upi"
    assert c.post("/payment/webhook", data=b"{}").status_code == 404

    sent = []
    monkeypatch.setattr("routes.payment.send_email", lambda *args: sent.append(args))
    with app.app_context():
        sub_id = Subscription.query.filter_by(order_id=order["order_id"]).one().id

    assert js(c, "/payment/direct-upi-verify", {
        "order_id": order["order_id"], "utr_number": "987654321012"
    }, t).status_code == 200
    admin_client, admin_token = admin()
    post(admin_client, f"/admin/payments/{sub_id}/approve", {}, admin_token)
    with app.app_context():
        sub = db.session.get(Subscription, sub_id)
        activated_at = sub.starts_at
        assert sub.payment_status == "paid" and sub.is_active
        assert SubscriptionActivation.query.filter_by(subscription_id=sub_id).count() == 1

    post(admin_client, f"/admin/payments/{sub_id}/approve", {}, admin_token)
    with app.app_context():
        sub = db.session.get(Subscription, sub_id)
        assert sub.starts_at == activated_at
        assert SubscriptionActivation.query.filter_by(subscription_id=sub_id).count() == 1
    assert len(sent) == 1


def test_upi_order_requires_server_configuration(monkeypatch):
    monkeypatch.setitem(app.config, "UPI_ID", "")
    c, t = student("noupiid1@t.co")
    response = js(c, "/payment/create-order", {"plan_type": "monthly"}, t)
    assert response.status_code == 503
    assert "Set UPI_ID" in response.get_json()["error"]
def test_ai_provider_errors_are_actionable():
    from routes.api import ai_error_response

    class ProviderError(Exception):
        def __init__(self, message, status_code):
            super().__init__(message)
            self.status_code = status_code

    class APITimeoutError(Exception):
        pass

    cases = [
        (ProviderError("Incorrect API key", 401), 401, "API key"),
        (ProviderError("Quota exceeded", 429), 429, "quota"),
        (ProviderError("The model does not exist", 404), 400, "OPENAI_MODEL"),
        (APITimeoutError("Request timed out"), 504, "timed out"),
    ]
    with app.test_request_context("/"):
        for exc, expected_status, expected_text in cases:
            response, status = ai_error_response(exc)
            assert status == expected_status
            assert expected_text.lower() in response.get_json()["error"].lower()


def test_legacy_user_schema_migrates_without_dropping_data(tmp_path):
    from schema_migrations import migrate_legacy_columns

    engine = create_engine(f"sqlite:///{(tmp_path / 'legacy.sqlite').as_posix()}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(120) NOT NULL)"
        )
        connection.exec_driver_sql("INSERT INTO users (id, email) VALUES (1, 'existing@t.co')")
    migrate_legacy_columns(engine)
    columns = {column['name'] for column in inspect(engine).get_columns('users')}
    with engine.connect() as connection:
        existing_email = connection.exec_driver_sql("SELECT email FROM users WHERE id = 1").scalar_one()
    assert {'email_verified_at', 'consent_at'} <= columns
    assert existing_email == 'existing@t.co'
    engine.dispose()


def test_scanned_pdf_without_ai_key_shows_clear_message():
    student("scanpdf1@t.co")
    from pypdf import PdfWriter

    pdf_buffer = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(pdf_buffer)
    c = app.test_client()
    token_value = token(c, "/auth/login")
    post(c, "/auth/login", {"email": "scanpdf1@t.co", "password": PW}, token_value)
    csrf_token = token(c, "/student/workspace")
    response = c.post(
        "/student/workspace/upload",
        data={
            "file": (io.BytesIO(pdf_buffer.getvalue()), "scan.pdf"),
            "subject": "Biology",
            "csrf_token": csrf_token
        },
        content_type="multipart/form-data",
        follow_redirects=True
    )
    assert b"transcription requires OPENAI_API_KEY" in response.data
    with app.app_context():
        document = Document.query.filter_by(original_name="scan.pdf").one()
        assert not document.is_processed
        assert document.content_text.startswith("[Scanned PDF detected")
