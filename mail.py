import smtplib
from email.message import EmailMessage
from flask import current_app


def send_email(recipient, subject, body):
    """Send via configured SMTP or print the message for local development."""
    host = current_app.config.get('SMTP_HOST')
    if not host:
        current_app.logger.info('SMTP is not configured; email to %s:\n%s', recipient, body)
        print(f'\n--- SmartStudy.ai email to {recipient} ---\nSubject: {subject}\n\n{body}\n--- end email ---\n')
        return False

    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = current_app.config['MAIL_FROM']
    message['To'] = recipient
    message.set_content(body)

    port = current_app.config['SMTP_PORT']
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=15) as smtp:
                _send(smtp, current_app.config, message)
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                smtp.starttls()
                _send(smtp, current_app.config, message)
    except (OSError, smtplib.SMTPException):
        current_app.logger.exception('Failed to send email to %s', recipient)
        return False
    return True


def _send(smtp, config, message):
    username = config.get('SMTP_USER')
    password = config.get('SMTP_PASSWORD')
    if username:
        smtp.login(username, password or '')
    smtp.send_message(message)
