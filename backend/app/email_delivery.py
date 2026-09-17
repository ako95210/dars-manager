from __future__ import annotations

import html
import smtplib
import ssl
from email.message import EmailMessage

from .config import settings


class EmailDeliveryError(RuntimeError):
    pass


def email_delivery_configured() -> bool:
    if not settings.smtp_host or not settings.email_from:
        return False
    return not settings.smtp_username or bool(settings.smtp_password)


def send_account_invitation(email: str, token: str) -> None:
    if not email_delivery_configured():
        raise EmailDeliveryError("Le service d’envoi d’e-mails n’est pas configuré.")

    invitation_url = f"{settings.frontend_origin}/#invitation={token}"
    message = EmailMessage()
    message["Subject"] = "Activez votre compte Dars Manager"
    message["From"] = settings.email_from
    message["To"] = email
    message.set_content(
        "Bonjour,\n\n"
        "Un compte Dars Manager a été créé pour vous. "
        f"Activez-le dans les {settings.invitation_ttl_seconds // 3600} heures, "
        "puis choisissez votre nom affiché et votre mot de passe :\n\n"
        f"{invitation_url}\n\n"
        "Si vous n’attendiez pas cette invitation, ignorez ce message."
    )
    safe_url = html.escape(invitation_url, quote=True)
    message.add_alternative(
        "<html><body>"
        "<p>Bonjour,</p>"
        "<p>Un compte Dars Manager a été créé pour vous.</p>"
        "<p>Vous choisirez votre nom affiché et votre mot de passe lors de l’activation.</p>"
        f'<p><a href="{safe_url}">Activer mon compte</a></p>'
        f"<p>Ce lien expire dans {settings.invitation_ttl_seconds // 3600} heures.</p>"
        "<p>Si vous n’attendiez pas cette invitation, ignorez ce message.</p>"
        "</body></html>",
        subtype="html",
    )

    try:
        if settings.smtp_ssl:
            smtp_connection = smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=15,
                context=ssl.create_default_context(),
            )
        else:
            smtp_connection = smtplib.SMTP(
                settings.smtp_host,
                settings.smtp_port,
                timeout=15,
            )
        with smtp_connection as smtp:
            if settings.smtp_starttls and not settings.smtp_ssl:
                smtp.starttls(context=ssl.create_default_context())
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailDeliveryError("L’e-mail d’invitation n’a pas pu être envoyé.") from exc
