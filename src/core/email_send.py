"""Transactional email: the SMTP transport, and the four messages the kernel itself sends.

Two layers, and the split is the point:

* :func:`deliver_smtp` is the raw stdlib :mod:`smtplib` transport (optional STARTTLS, sent
  synchronously). Nothing but the layer above should call it.
* :func:`send` is what a message function calls. It records a ledger row, hands the message off,
  and tracks its delivery status, so a transactional email is retried with backoff and
  dead-lettered rather than lost on a transient failure.

When ``SMTP_HOST`` is unset — the development default — nothing is sent and no row is recorded;
:func:`send` returns ``False`` and the caller logs the link instead. That is what makes the
sign-up and password-reset flows usable locally with no mail server.

The four messages below are the ones authentication cannot work without: activation, password
reset, email-change verification and the one-time code. Yours go at the bottom of this file.
"""

from __future__ import annotations

import html as html_module
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import make_msgid

from src.commons.enums import AppEnvironment, NotificationTemplate
from src.core.config import get_settings

logger = logging.getLogger(__name__)


class EmailDeliveryError(Exception):
    """Raised when SMTP is configured but sending failed (network, DNS, auth, etc.)."""


def deliver_smtp(
    *,
    to: str,
    subject: str,
    text: str,
    html: str | None = None,
    from_email: str | None = None,
    list_unsubscribe_url: str | None = None,
) -> str:
    """Hand one email to SMTP and return the ``Message-ID`` used.

    The raw transport behind the notification service's email channel. The returned Message-ID is
    recorded as the notification's ``provider_message_id`` so a later delivery-status webhook can
    be correlated back to it. When ``list_unsubscribe_url`` is given (non-essential mail, Issue #72),
    RFC 2369 ``List-Unsubscribe`` and RFC 8058 ``List-Unsubscribe-Post`` headers are added so the
    recipient's mail client can offer one-click unsubscribe honoured without a login. Raises
    :class:`EmailDeliveryError` when ``SMTP_HOST`` is unset or the SMTP/socket/TLS exchange fails.
    """
    settings = get_settings()
    host = (settings.smtp_host or "").strip()
    if not host:
        raise EmailDeliveryError("SMTP is not configured (SMTP_HOST is unset)")

    from_addr = (from_email or settings.smtp_from_email).strip()
    to_addr = to.strip()
    message_id = make_msgid()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Message-ID"] = message_id
    if list_unsubscribe_url:
        # RFC 2369 + RFC 8058: a mail client renders a native "Unsubscribe" control and can POST
        # one-click to the URL, which the login-free endpoint honours.
        msg["List-Unsubscribe"] = f"<{list_unsubscribe_url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.attach(MIMEText(text, "plain"))
    if html:
        msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(host, settings.smtp_port) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_user and settings.smtp_password:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.sendmail(from_addr, [to_addr], msg.as_string())
        logger.info("Email sent to %s: %s", to_addr, subject)
        return message_id
    except EmailDeliveryError:
        raise
    except Exception as exc:
        # Broad on purpose: every SMTP/socket/TLS failure is re-raised as one domain error.
        logger.exception("Failed to send email to %s: %s", to_addr, exc)
        raise EmailDeliveryError from exc


def send(
    *,
    to: str,
    subject: str,
    text: str,
    html: str | None = None,
    from_email: str | None = None,
    template: NotificationTemplate = NotificationTemplate.GENERIC,
) -> bool:
    """Send one transactional email through the notification service.

    Returns ``True`` when the message was recorded and handed off for delivery, ``False`` when
    ``SMTP_HOST`` is unset (development fallback — nothing sent, no ledger row). Raises
    :class:`EmailDeliveryError` when SMTP is configured but delivery fails, so a caller can log or
    retry. ``template`` keys the ledger row, so delivery status is queryable per message type.
    """
    settings = get_settings()
    if not (settings.smtp_host or "").strip():
        return False

    # Surface the event in the recipient's in-app notification centre (Issue #113): an event in a
    # centre category becomes an in-app notification honouring the recipient's per-category
    # preference; auth codes are never recorded. Best-effort in its own short transaction (never
    # raises), and on the same SMTP-configured path as the delivery ledger — so it shares that
    # path's DB context rather than firing on the no-SMTP development fallback above.
    from src.commons.enums import notification_category_for
    from src.modules.notifications import center

    center.record_from_email_safely(
        email=to,
        category=notification_category_for(template),
        title=subject,
        body=text,
    )

    # Imported lazily: the notification service imports this module's transport, so a top-level
    # import here would be circular.
    from src.modules.notifications import service as notification_service

    notification_service.deliver_email(
        to=to,
        subject=subject,
        text=text,
        html=html,
        from_email=from_email,
        template=template,
    )
    return True


def send_activation_email(
    to_email: str,
    activation_link: str,
    expire_hours: int | None = None,
) -> None:
    """Send an account activation email (HTML + plain text).

    In development without SMTP configured, logs the activation link instead of sending.
    """
    settings = get_settings()
    app_name = settings.app_name
    hours = expire_hours or settings.activation_link_expire_hours
    subject = f"Activate your {app_name} account"
    plain_body = f"""Hello,

Thank you for signing up for {app_name}.

To activate your account, please click the link below:

{activation_link}

This link will expire in {hours} hours. If you did not create an account, you can safely ignore this email.

Best regards,
The {app_name} Team
"""
    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Activate your account</title>
<style>
  body {{ font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; line-height: 1.6; color: #1a1d2e; max-width: 600px; margin: 0 auto; padding: 24px; background: #f5f5f7; }}
  .card {{ background: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 2px 12px rgba(0,0,0,0.06); }}
  .header {{ background: linear-gradient(135deg, #0e7c5a 0%, #0a5d43 100%); color: #fff; padding: 28px 32px; text-align: center; }}
  .header h1 {{ margin: 0; font-size: 1.5rem; font-weight: 600; letter-spacing: 0.02em; }}
  .body {{ padding: 32px; }}
  .body p {{ margin: 0 0 16px; color: #3d4257; }}
  .cta-wrap {{ text-align: center; margin: 28px 0; }}
  .cta {{ display: inline-block; background: #0e7c5a; color: #ffffff !important; padding: 14px 28px; text-decoration: none; border-radius: 12px; font-weight: 600; font-size: 1rem; }}
  .link-fallback {{ margin-top: 20px; font-size: 0.875rem; color: #6b7280; word-break: break-all; }}
  .footer {{ padding: 24px 32px; border-top: 1px solid #e8eaf0; background: #f9fafb; font-size: 0.875rem; color: #6b7280; }}
  .company {{ font-weight: 600; color: #1a1d2e; margin-bottom: 4px; }}
</style>
</head>
<body>
<div class="card">
  <div class="header">
    <h1>{app_name}</h1>
  </div>
  <div class="body">
    <p>Hello,</p>
    <p>Thank you for signing up for <strong>{app_name}</strong>. To activate your account, click the button below.</p>
    <div class="cta-wrap">
      <a href="{activation_link}" class="cta">Activate my account</a>
    </div>
    <p>This link expires in <strong>{hours} hours</strong>. If you did not create an account, you can safely ignore this email.</p>
    <p class="link-fallback">If the button does not work, copy and paste this link into your browser:<br>{activation_link}</p>
  </div>
  <div class="footer">
    <p class="company">{app_name}</p>
    <p>Best regards,<br>The {app_name} Team</p>
  </div>
</div>
</body>
</html>
"""
    sent = send(
        to=to_email,
        subject=subject,
        text=plain_body,
        html=html_body,
        template=NotificationTemplate.ACCOUNT_ACTIVATION,
    )
    if not sent:
        logger.info(
            "Email (SMTP not configured) to %s — %s. Set SMTP_HOST etc. to send.",
            to_email,
            subject,
        )
        logger.info("Activation link (dev): %s", activation_link)


def send_staff_invitation_email(
    to_email: str,
    invite_link: str,
    role: str,
    expire_hours: int | None = None,
) -> None:
    """Send a staff invitation (HTML + plain text) — Issue 22.

    The link is a credential: whoever opens it sets the password for the account it creates. So it
    is never logged outside development, and the copy says plainly what to do if the invitation was
    unexpected — an invitation that arrives out of the blue is the shape a social-engineering
    attempt takes.
    """
    settings = get_settings()
    app_name = settings.app_name
    hours = expire_hours or settings.staff_invite_expire_hours
    from src.core.rbac_language import role_label

    readable_role = role_label(role)
    subject = f"You have been invited to {app_name}"
    plain_body = f"""Hello,

You have been invited to join a clinic on {app_name} as a {readable_role}.

To accept and choose your password, open the link below:

{invite_link}

This invitation expires in {hours} hours, and can be used once. If you were not expecting it,
ignore this email and tell the clinic that invited you.

Best regards,
The {app_name} Team
"""
    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>You have been invited</title>
<style>
  body {{ font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; line-height: 1.6; color: #1a1d2e; max-width: 600px; margin: 0 auto; padding: 24px; background: #f5f5f7; }}
  .card {{ background: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 2px 12px rgba(0,0,0,0.06); }}
  .header {{ background: linear-gradient(135deg, #0e7c5a 0%, #0a5d43 100%); color: #fff; padding: 28px 32px; text-align: center; }}
  .header h1 {{ margin: 0; font-size: 1.5rem; font-weight: 600; letter-spacing: 0.02em; }}
  .body {{ padding: 32px; }}
  .body p {{ margin: 0 0 16px; color: #3d4257; }}
  .cta-wrap {{ text-align: center; margin: 28px 0; }}
  .cta {{ display: inline-block; background: #0e7c5a; color: #ffffff !important; padding: 14px 28px; text-decoration: none; border-radius: 12px; font-weight: 600; font-size: 1rem; }}
  .link-fallback {{ margin-top: 20px; font-size: 0.875rem; color: #6b7280; word-break: break-all; }}
  .footer {{ padding: 24px 32px; border-top: 1px solid #e8eaf0; background: #f9fafb; font-size: 0.875rem; color: #6b7280; }}
  .company {{ font-weight: 600; color: #1a1d2e; margin-bottom: 4px; }}
</style>
</head>
<body>
<div class="card">
  <div class="header">
    <h1>{app_name}</h1>
  </div>
  <div class="body">
    <p>Hello,</p>
    <p>You have been invited to join a clinic on <strong>{app_name}</strong> as a
       <strong>{readable_role}</strong>. Accept the invitation to choose your password.</p>
    <div class="cta-wrap">
      <a href="{invite_link}" class="cta">Accept the invitation</a>
    </div>
    <p>This invitation expires in <strong>{hours} hours</strong> and can be used once. If you were
       not expecting it, ignore this email and tell the clinic that invited you.</p>
    <p class="link-fallback">If the button does not work, copy and paste this link into your browser:<br>{invite_link}</p>
  </div>
  <div class="footer">
    <p class="company">{app_name}</p>
    <p>Best regards,<br>The {app_name} Team</p>
  </div>
</div>
</body>
</html>
"""
    sent = send(
        to=to_email,
        subject=subject,
        text=plain_body,
        html=html_body,
        template=NotificationTemplate.STAFF_INVITATION,
    )
    if not sent:
        logger.info(
            "Email (SMTP not configured) to %s — %s. Set SMTP_HOST etc. to send.",
            to_email,
            subject,
        )
        if settings.environment is AppEnvironment.DEVELOPMENT:
            # Development only: without it there is no way to accept an invitation locally. A
            # deployment with no SMTP configured is a misconfiguration, not a reason to write a
            # credential into the log.
            logger.info("Invitation link (dev): %s", invite_link)


def send_password_reset_email(
    to_email: str,
    reset_link: str,
    expire_hours: int | None = None,
) -> None:
    """Send a password reset email with a secure, expiring link (HTML + plain text).

    In development without SMTP configured, logs the reset link instead of sending.
    """
    settings = get_settings()
    app_name = settings.app_name
    hours = expire_hours or settings.password_reset_link_expire_hours
    subject = f"Reset your {app_name} password"
    plain_body = f"""Hello,

We received a request to reset your {app_name} password.

Use the secure link below to choose a new password:

{reset_link}

This link will expire in {hours} hours. If you did not request this reset, you can safely ignore this email.

Best regards,
The {app_name} Team
"""
    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Reset your password</title>
<style>
  body {{ font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; line-height: 1.6; color: #1a1d2e; max-width: 600px; margin: 0 auto; padding: 24px; background: #f5f5f7; }}
  .card {{ background: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 2px 12px rgba(0,0,0,0.06); }}
  .header {{ background: linear-gradient(135deg, #0e7c5a 0%, #0a5d43 100%); color: #fff; padding: 28px 32px; text-align: center; }}
  .header h1 {{ margin: 0; font-size: 1.5rem; font-weight: 600; letter-spacing: 0.02em; }}
  .body {{ padding: 32px; }}
  .body p {{ margin: 0 0 16px; color: #3d4257; }}
  .cta-wrap {{ text-align: center; margin: 28px 0; }}
  .cta {{ display: inline-block; background: #0e7c5a; color: #ffffff !important; padding: 14px 28px; text-decoration: none; border-radius: 12px; font-weight: 600; font-size: 1rem; }}
  .link-fallback {{ margin-top: 20px; font-size: 0.875rem; color: #6b7280; word-break: break-all; }}
  .footer {{ padding: 24px 32px; border-top: 1px solid #e8eaf0; background: #f9fafb; font-size: 0.875rem; color: #6b7280; }}
  .company {{ font-weight: 600; color: #1a1d2e; margin-bottom: 4px; }}
</style>
</head>
<body>
<div class="card">
  <div class="header">
    <h1>{app_name}</h1>
  </div>
  <div class="body">
    <p>Hello,</p>
    <p>We received a request to reset your <strong>{app_name}</strong> password. Click the button below to choose a new one.</p>
    <div class="cta-wrap">
      <a href="{reset_link}" class="cta">Reset my password</a>
    </div>
    <p>This link expires in <strong>{hours} hours</strong>. If you did not request this reset, you can safely ignore this email.</p>
    <p class="link-fallback">If the button does not work, copy and paste this link into your browser:<br>{reset_link}</p>
  </div>
  <div class="footer">
    <p class="company">{app_name}</p>
    <p>Best regards,<br>The {app_name} Team</p>
  </div>
</div>
</body>
</html>
"""
    sent = send(
        to=to_email,
        subject=subject,
        text=plain_body,
        html=html_body,
        template=NotificationTemplate.PASSWORD_RESET,
    )
    if not sent:
        logger.info(
            "Email (SMTP not configured) to %s — %s. Set SMTP_HOST etc. to send.",
            to_email,
            subject,
        )
        logger.info("Password reset link (dev): %s", reset_link)


def send_email_change_verification_email(
    to_email: str,
    confirm_link: str,
    expire_hours: int | None = None,
) -> None:
    """Send a confirmation email to a user's *new* address before an email change takes effect.

    The link is sent to the requested new address (not the current one), so the change only
    completes once the user proves control of it (Issue #59). In development without SMTP
    configured, logs the confirmation link instead of sending.
    """
    settings = get_settings()
    app_name = settings.app_name
    hours = expire_hours or settings.email_change_link_expire_hours
    subject = f"Confirm your new {app_name} email address"
    plain_body = f"""Hello,

We received a request to change the email address on your {app_name} account to this one.

To confirm the change, please click the link below:

{confirm_link}

This link will expire in {hours} hours. Until you confirm, your account keeps its current email address. If you did not request this change, you can safely ignore this email.

Best regards,
The {app_name} Team
"""
    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Confirm your new email address</title>
<style>
  body {{ font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; line-height: 1.6; color: #1a1d2e; max-width: 600px; margin: 0 auto; padding: 24px; background: #f5f5f7; }}
  .card {{ background: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 2px 12px rgba(0,0,0,0.06); }}
  .header {{ background: linear-gradient(135deg, #0e7c5a 0%, #0a5d43 100%); color: #fff; padding: 28px 32px; text-align: center; }}
  .header h1 {{ margin: 0; font-size: 1.5rem; font-weight: 600; letter-spacing: 0.02em; }}
  .body {{ padding: 32px; }}
  .body p {{ margin: 0 0 16px; color: #3d4257; }}
  .cta-wrap {{ text-align: center; margin: 28px 0; }}
  .cta {{ display: inline-block; background: #0e7c5a; color: #ffffff !important; padding: 14px 28px; text-decoration: none; border-radius: 12px; font-weight: 600; font-size: 1rem; }}
  .link-fallback {{ margin-top: 20px; font-size: 0.875rem; color: #6b7280; word-break: break-all; }}
  .footer {{ padding: 24px 32px; border-top: 1px solid #e8eaf0; background: #f9fafb; font-size: 0.875rem; color: #6b7280; }}
  .company {{ font-weight: 600; color: #1a1d2e; margin-bottom: 4px; }}
</style>
</head>
<body>
<div class="card">
  <div class="header">
    <h1>{app_name}</h1>
  </div>
  <div class="body">
    <p>Hello,</p>
    <p>We received a request to change the email address on your <strong>{app_name}</strong> account to this one. Click the button below to confirm the change.</p>
    <div class="cta-wrap">
      <a href="{confirm_link}" class="cta">Confirm my new email</a>
    </div>
    <p>This link expires in <strong>{hours} hours</strong>. Until you confirm, your account keeps its current email address. If you did not request this change, you can safely ignore this email.</p>
    <p class="link-fallback">If the button does not work, copy and paste this link into your browser:<br>{confirm_link}</p>
  </div>
  <div class="footer">
    <p class="company">{app_name}</p>
    <p>Best regards,<br>The {app_name} Team</p>
  </div>
</div>
</body>
</html>
"""
    sent = send(
        to=to_email,
        subject=subject,
        text=plain_body,
        html=html_body,
        template=NotificationTemplate.EMAIL_CHANGE_VERIFICATION,
    )
    if not sent:
        logger.info(
            "Email (SMTP not configured) to %s — %s. Set SMTP_HOST etc. to send.",
            to_email,
            subject,
        )
        logger.info("Email-change confirmation link (dev): %s", confirm_link)


def send_otp_email(to_email: str, code: str, client_ip: str | None = None) -> None:
    """Send a one-time sign-in code (HTML + plain text).

    In development without SMTP configured, logs the code instead of sending.
    """
    settings = get_settings()
    app_name = settings.app_name
    ttl = settings.otp_ttl_minutes
    subject = f"Your {app_name} sign-in code"
    safe_code = html_module.escape(code)
    ip_line = (client_ip.strip() if client_ip else None) or "Unavailable"
    safe_ip = html_module.escape(ip_line)
    plain_body = f"""Your one-time sign-in code is: {code}

This code expires in {ttl} minutes. Do not share it.

Sign-in attempt details:
- Client IP: {ip_line}

If you did not request this code, you can safely ignore this email.

Best regards,
The {app_name} Team
"""
    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Your sign-in code</title>
<style>
  body {{ font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; line-height: 1.6; color: #1a1d2e; max-width: 600px; margin: 0 auto; padding: 24px; background: #f5f5f7; }}
  .card {{ background: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 2px 12px rgba(0,0,0,0.06); }}
  .header {{ background: linear-gradient(135deg, #0e7c5a 0%, #0a5d43 100%); color: #fff; padding: 28px 32px; text-align: center; }}
  .header h1 {{ margin: 0; font-size: 1.5rem; font-weight: 600; letter-spacing: 0.02em; }}
  .body {{ padding: 32px; }}
  .body p {{ margin: 0 0 16px; color: #3d4257; }}
  .code {{ text-align: center; font-size: 2.25rem; font-weight: 800; letter-spacing: 0.2em; color: #0e7c5a; margin: 24px 0; }}
  .meta {{ border: 1px solid #e8eaf0; border-radius: 12px; padding: 14px 16px; background: #f9fafb; font-size: 0.875rem; color: #3d4257; }}
  .footer {{ padding: 24px 32px; border-top: 1px solid #e8eaf0; background: #f9fafb; font-size: 0.875rem; color: #6b7280; }}
  .company {{ font-weight: 600; color: #1a1d2e; margin-bottom: 4px; }}
</style>
</head>
<body>
<div class="card">
  <div class="header">
    <h1>{app_name}</h1>
  </div>
  <div class="body">
    <p>Use this one-time code to complete your sign-in:</p>
    <div class="code">{safe_code}</div>
    <p>This code expires in <strong>{ttl} minutes</strong>. Do not share it.</p>
    <div class="meta"><strong>Client IP:</strong> {safe_ip}</div>
    <p style="margin-top:18px;">If you did not request this code, you can safely ignore this email.</p>
  </div>
  <div class="footer">
    <p class="company">{app_name}</p>
    <p>Best regards,<br>The {app_name} Team</p>
  </div>
</div>
</body>
</html>
"""
    sent = send(
        to=to_email,
        subject=subject,
        text=plain_body,
        html=html_body,
        template=NotificationTemplate.OTP_SIGN_IN,
    )
    if not sent:
        # Never the code in a log (Issue 17): in development it goes to /dev/outbox instead.
        from src.commons.enums import NotificationChannel
        from src.modules.notifications import dev_outbox

        dev_outbox.record(NotificationChannel.EMAIL, to_email, plain_body)
        logger.info(
            "Sign-in code email not sent (SMTP not configured); in development it is at "
            "/dev/outbox. Set SMTP_HOST etc. to send."
        )


# ── your emails ──────────────────────────────────────────────────────────────
#
# One function per message, each building its own subject and HTML and handing both to
# :func:`send`. Keep the transport out of them: `send` is the only thing that talks to SMTP, so a
# new message never has to think about STARTTLS, a missing SMTP_HOST, or how a delivery failure is
# recorded. A message that must survive a transient failure goes through the notifications module
# instead, which retries and dead-letters; this path is for the ones that are sent inline with a
# request and are safe to lose.
