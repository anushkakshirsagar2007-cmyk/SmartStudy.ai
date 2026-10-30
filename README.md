# SmartStudy.ai – Your AI-Powered Study Partner 🧠

A comprehensive, full-stack AI-driven educational web platform designed to help students learn faster, master complex concepts, test their knowledge with interactive quizzes, and organize study sessions.

---

## 🌟 Key Features

### 1. ⚡ AI Study Workspace
- **Multi-Format Document Upload**: Support for PDF, TXT, DOCX, and Markdown files.
- Scanned PDFs fall back to bounded-page image transcription using `pypdfium2` and the configured vision model; local OCR is also used when optional `pytesseract` and its Tesseract executable are installed.
- **AI Summary & Structured Notes**: Generates concise takeaways and revision notes automatically.
- **AI Quiz Generation**: Generates multiple-choice questions with answers and detailed explanations.
- **AI Flashcards**: Creates smart spaced-repetition decks ready for active recall study.

### 2. 🎴 3D Interactive Flashcards
- Realistic 3D flip card animation with keyboard shortcuts (`Space` to flip, `←`/`→` to navigate).
- Spaced repetition tracking: mark cards as mastered with real-time deck progress.
- One-click shuffle to test memory retention.

### 3. 🎯 Practice Quizzes & Knowledge Checks
- Timed practice exams with real-time question progress tracker.
- Instant score calculation, motivational performance banner, and detailed question breakdowns with explanations.
- Full quiz attempt history and analytics.

### 4. 📅 Study Planner & Kanban Board
- Interactive task management columns: **To Do**, **In Progress**, and **Completed**.
- Priority badges (High, Medium, Low), deadlines, and subject categorization.
- Real-time task status transitions and quick creation modal.

### 5. 🤖 24/7 AI Study Tutor Chat
- Contextual grounding: Chat directly with your uploaded documents or ask general academic questions.
- Quick prompt chips: *Explain Simply*, *Key Takeaways*, *Quiz Me*, *Real-World Analogy*.
- Clear error messages when the AI key is missing, invalid or rate-limited.

### 6. 📊 Analytics & Progress Tracking
- 7-day study activity visual charts.
- Subject concentration breakdown and task completion rates.
- Quiz score trends over time.

### 7. 🛡️ Role-Based Access & Admin Panel
- **Student Dashboard**: Personalized KPIs, recent notes, tasks, and quick actions.
- **Admin Dashboard**: System-wide statistics, active student management, content moderation, and platform analytics.

---

## 🚀 Quick Start Guide

### 1. Prerequisites
- Python 3.10+ (Tested on Python 3.14)
- Pip package manager

### 2. Open Project Folder
The complete project files are located at:
```text
SmartStudy.ai
```

### 3. Run the Application
In your terminal, navigate to the folder and run:
```bash
# Set UTF-8 encoding (Windows Powershell)
$env:PYTHONIOENCODING="utf-8"

# Start the Flask development server
python app.py
```

The application will start at:
👉 **[http://127.0.0.1:5000](http://127.0.0.1:5000)**

---

## 🔑 Admin account
Set `ADMIN_EMAIL` and `ADMIN_PASSWORD` in `.env`, then run `python init_db.py`. If you leave the password empty, a random one is generated and printed once. There are no default or demo accounts.

## 📁 Project Architecture

```text
smartstudy/
├── app.py                  # Application entry point
├── extensions.py           # Flask app factory, SQLAlchemy & LoginManager
├── models.py               # Database schemas (User, Note, Quiz, Flashcard, Task, etc.)
├── init_db.py              # Database seeding script with sample materials
├── requirements.txt        # Python dependency manifest
├── .env                    # Environment configuration
├── routes/
│   ├── main.py             # Landing page route
│   ├── auth.py             # Login, register, logout, authentication
│   ├── student.py          # Dashboard, notes, quizzes, flashcards, planner, chat
│   ├── admin.py            # Admin dashboard, student directory, content moderation
│   └── api.py              # AI generation APIs, chat endpoint, task updates
├── static/
│   ├── css/style.css       # Custom modern purple & white theme design system
│   └── js/main.js          # Core frontend interactivity, modals, toast alerts
├── templates/
│   ├── base.html           # Main layout with responsive sidebar and topbar
│   ├── index.html          # High-converting landing page
│   ├── auth/               # Login & Register views
│   ├── student/            # Dashboard, Workspace, Notes, Quizzes, Flashcards, Planner, Chat
│   └── admin/              # Admin analytics, students directory, moderation
└── uploads/                # Secure local storage for student study documents
```


## 💳 Payments
- **Manual UPI only:** set `UPI_ID` to the UPI account that receives payments. Students pay the displayed amount and submit the 12-digit UTR. A plan remains inactive until an admin verifies receipt and approves it in **Admin → Payments**. No payment gateway or automatic renewal is used.
- If `UPI_ID` is unset, checkout is disabled and displays a configuration error rather than showing a placeholder payment destination.
- Set `BUSINESS_NAME`, `CONTACT_EMAIL`, and `REFUND_WINDOW` to customize the business identity and refund-policy wording. Configure the `SMTP_*` settings and `MAIL_FROM` to deliver verification, reset, and receipt emails. If `SMTP_HOST` is empty, messages (including their links) are printed to the server console for development.

## ✉️ Account email and database updates
- Registration requires consent to the Terms and Privacy Policy and records its timestamp.
- New accounts receive a signed email-verification link (valid for 24 hours); signed-in unverified users see a banner with a rate-limited resend action.
- Password recovery uses a generic response, a hashed single-use token, and a 30-minute expiry. Do not share reset links or console output.
- Existing databases are updated without deleting data when you run `python init_db.py`; new tables are added and the nullable account metadata columns are added only when missing.
- Scanned-PDF image processing is capped by `SCANNED_PDF_PAGE_LIMIT` (maximum five pages). `pytesseract` is optional and is not installed by requirements because it also needs a separately installed Tesseract executable.

## 🔒 Security notes
CSRF protection on all forms and fetch calls, HttpOnly/SameSite cookies, 10 MB upload limit, `SECRET_KEY` required, debug off by default. Never commit `.env`, `instance/` or `uploads/`.

## 🚀 Deploying (PythonAnywhere free tier)
1. Upload the project, create a virtualenv, `pip install -r requirements.txt`.
2. Set environment variables in the WSGI file (`SECRET_KEY`, `OPENAI_API_KEY`, `FLASK_DEBUG=False`, `COOKIE_SECURE=1`, ...), then `from app import app as application`.
3. Run `python init_db.py` in a console, then Reload the web app.
Free accounts can only reach whitelisted sites: confirm your AI provider's domain is allowed.


## 🛡️ Login limits, backups, SEO
- 5 failed logins per email+IP locks login for 5 minutes. Logout is a POST (CSRF-safe).
- **Forgot password?** Use the password-recovery link on the sign-in page. Reset requests and verification email resends are rate-limited.
- **Backups:** `python backup.py` creates `backups/backup-<time>.zip` (database + uploads, last 14 kept). Schedule it daily.
- **Search engines:** set `SITE_URL` in `.env`; `/robots.txt` and `/sitemap.xml` are generated. Submit the sitemap in Google Search Console.
- **Legal pages** (`/terms`, `/privacy`, `/refund`) are plain templates: review and edit them for your situation before accepting real payments.
- **Tests:** install `requirements.txt`, then run `pytest -q`. Install the browser once with `python -m playwright install chromium` to run the real-browser E2E test. The live AI integration test is skipped unless `OPENAI_API_KEY` is set and makes billable provider calls.

## ✅ Launch checklist
Complete and verify these items before accepting real users or payments:

- Set `UPI_ID` to the correct receiving account, confirm the account is authorized to receive business payments, and test student UTR submission and admin approval end to end.
- Confirm with a qualified tax professional which GST registration, tax collection, and invoice requirements apply to the business and the specific services; implement the required invoicing and records.
- Have the Terms, Privacy Policy, and refund wording reviewed by a lawyer for the operating jurisdiction and actual product practices.
- Confirm SMTP delivery, account recovery, email verification, payment receipts, backups, HTTPS, and production cookie settings.

This checklist is operational guidance only; completing it does not establish or guarantee legal, tax, or regulatory compliance.
