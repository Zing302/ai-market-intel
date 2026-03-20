import os
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from utils.logger import get_logger

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../config/.env"))

logger = get_logger("email_sender")


def send_email(subject: str, body: str) -> None:
    email_from = os.getenv("EMAIL_FROM")
    email_to = os.getenv("EMAIL_TO")
    app_password = os.getenv("EMAIL_APP_PASSWORD")

    if not all([email_from, email_to, app_password]):
        logger.error("Missing email credentials in .env (EMAIL_FROM, EMAIL_TO, EMAIL_APP_PASSWORD).")
        raise ValueError("Incomplete email configuration.")

    msg = MIMEMultipart()
    msg["From"] = email_from
    msg["To"] = email_to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(email_from, app_password)
            server.sendmail(email_from, email_to, msg.as_string())
        logger.info(f"Email sent successfully to {email_to} — subject: '{subject}'")
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        raise
