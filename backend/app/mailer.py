from email.message import EmailMessage
import os
from pathlib import Path
import smtplib
import ssl
import uuid


class MailDeliveryError(Exception):
    pass


class Mailer:
    def __init__(self, settings):
        self.settings = settings

    def send_verification(self, email, code):
        message = EmailMessage()
        message["From"] = self.settings.mail_from
        message["To"] = email
        message["Subject"] = "Подтверждение почты — проект А1"
        message.set_content(
            f"Ваш код подтверждения: {code}\n\n"
            f"Код действует {self.settings.verification_ttl_seconds // 60} минут и используется один раз.\n"
            "Если вы не запрашивали регистрацию или подтверждение, проигнорируйте письмо.\n",
            cte="8bit",
        )
        try:
            if self.settings.mail_backend == "file":
                outbox = self.settings.database_path.parent / "outbox"
                outbox.mkdir(parents=True, exist_ok=True)
                path = outbox / (uuid.uuid4().hex + ".eml")
                descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(message.as_bytes())
                return
            context = ssl.create_default_context()
            if self.settings.smtp_security == "ssl":
                client = smtplib.SMTP_SSL(self.settings.smtp_host, self.settings.smtp_port, timeout=10, context=context)
            else:
                client = smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=10)
            with client:
                if self.settings.smtp_security == "starttls":
                    client.ehlo()
                    client.starttls(context=context)
                    client.ehlo()
                client.login(self.settings.smtp_username, self.settings.smtp_password)
                refused = client.send_message(message)
                if refused:
                    raise MailDeliveryError("Получатель отклонён")
        except (OSError, smtplib.SMTPException, UnicodeError) as exc:
            raise MailDeliveryError("Не удалось передать письмо") from exc
