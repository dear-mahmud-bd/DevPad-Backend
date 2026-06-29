"""
app/services/email.py

Sends transactional emails via SMTP (Mailpit in dev, real SMTP in production).
All email content is defined here — nowhere else builds email strings.

In development: Mailpit catches all emails at http://localhost:8025
In production:  Set SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD
                to your real mail provider (Resend, SendGrid, etc.)
"""
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.core.config import get_settings

settings = get_settings()


def _send(to_email: str, subject: str, html_body: str) -> None:
    """
    Low-level SMTP send. Called by all public functions below.
    Uses STARTTLS if SMTP_TLS=true, plain otherwise (Mailpit needs plain).
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
        if settings.smtp_tls:
            server.starttls()
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.sendmail(settings.smtp_from, to_email, msg.as_string())


def send_verification_email(to_email: str, username: str, token: str) -> None:
    """
    Sent on signup. The link calls GET /auth/verify-email?token={token}.
    Token expires in 24 hours (enforced in the auth service).
    """
    verify_url = f"http://localhost/auth/verify-email?token={token}"
    subject = "DevPad — Verify your email address"
    html = f"""
    <h2>Welcome to DevPad, {username}!</h2>
    <p>Click the button below to verify your email address.
       This link expires in <strong>24 hours</strong>.</p>
    <p>
      <a href="{verify_url}"
         style="background:#4f46e5;color:#fff;padding:12px 24px;
                border-radius:6px;text-decoration:none;font-weight:bold;">
        Verify Email
      </a>
    </p>
    <p>If you did not create a DevPad account, ignore this email.</p>
    """
    _send(to_email, subject, html)


def send_password_reset_email(to_email: str, username: str, token: str) -> None:
    """
    Sent on forgot-password request. The link calls POST /auth/reset-password.
    Token expires in 1 hour.
    """
    reset_url = f"http://localhost/auth/reset-password?token={token}"
    subject = "DevPad — Reset your password"
    html = f"""
    <h2>Password reset request</h2>
    <p>Hi <strong>{username}</strong>,</p>
    <p>We received a request to reset the password for your DevPad account.
       Click the button below to choose a new password.
       This link expires in <strong>1 hour</strong>.</p>
    <p>
      <a href="{reset_url}"
         style="background:#4f46e5;color:#fff;padding:12px 24px;
                border-radius:6px;text-decoration:none;font-weight:bold;">
        Reset Password
      </a>
    </p>
    <p>If you did not request a password reset, ignore this email — your
       password will not change.</p>
    """
    _send(to_email, subject, html)


def send_collaboration_invite(
    to_email: str,
    inviter_username: str,
    note_title: str,
    permission: str,
    token: str,
) -> None:
    """
    Sent when a note owner invites someone to view or edit a note.
    The link calls GET /notes/{note_id}/accept-invite?token={token}.
    Token expires in 72 hours.
    """
    accept_url = f"http://localhost/notes/accept-invite?token={token}"
    subject = f"DevPad — {inviter_username} shared a note with you"
    html = f"""
    <h2>You've been invited to collaborate</h2>
    <p><strong>{inviter_username}</strong> has given you
       <strong>{permission}</strong> access to the note
       "<em>{note_title}</em>".</p>
    <p>
      <a href="{accept_url}"
         style="background:#4f46e5;color:#fff;padding:12px 24px;
                border-radius:6px;text-decoration:none;font-weight:bold;">
        Accept Invitation
      </a>
    </p>
    <p>This link expires in <strong>72 hours</strong>.
       If you did not expect this, you can safely ignore it.</p>
    """
    _send(to_email, subject, html)
