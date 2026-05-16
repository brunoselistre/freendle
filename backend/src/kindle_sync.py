from __future__ import annotations

import io
import logging
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from .config import settings

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

_KINDLE_SUPPORTED = {
    "pdf", "epub", "doc", "docx", "txt", "rtf",
    "html", "htm", "png", "gif", "jpg", "jpeg", "bmp",
}


def _load_credentials() -> Credentials:
    return Credentials.from_authorized_user_file(str(settings.gmail_token_path), _SCOPES)


def send_to_kindle(filepath: Path) -> tuple[bool, str | None]:
    ext = filepath.suffix.lstrip(".").lower()
    if ext not in _KINDLE_SUPPORTED:
        return False, (
            f"'{ext}' not supported by Send-to-Kindle email. "
            f"Download the book as epub or pdf instead."
        )

    try:
        creds = _load_credentials()
        if not creds.valid and creds.expired and creds.refresh_token:
            creds.refresh(Request())

        file_size = filepath.stat().st_size
        if file_size > 25 * 1024 * 1024:
            return False, (
                f"File is {file_size / 1024 / 1024:.1f} MB — Gmail attachment limit is 25 MB. "
                "Convert to epub or a smaller format first."
            )
        logger.info("[kindle_sync] preparing %s (%d bytes)", filepath.name, file_size)

        msg = MIMEMultipart()
        msg["From"] = settings.gmail_sender
        msg["To"] = settings.kindle_email
        msg["Subject"] = "convert"
        msg.attach(MIMEText("", "plain"))

        with open(filepath, "rb") as f:
            attachment = MIMEApplication(f.read(), Name=filepath.name)
        attachment.add_header("Content-Disposition", "attachment", filename=filepath.name)
        msg.attach(attachment)

        raw_bytes = msg.as_bytes()
        logger.info(
            "[kindle_sync] MIME message built: file=%d bytes, message=%d bytes",
            file_size,
            len(raw_bytes),
        )

        service = build("gmail", "v1", credentials=creds)
        media = MediaIoBaseUpload(
            io.BytesIO(raw_bytes),
            mimetype="message/rfc822",
            resumable=False,
        )
        service.users().messages().send(
            userId="me",
            body={},
            media_body=media,
        ).execute()

        logger.info("[kindle_sync] sent %s to %s", filepath.name, settings.kindle_email)
        return True, None
    except Exception as exc:
        logger.error("[kindle_sync] failed to send %s: %s", filepath.name, exc)
        return False, str(exc)
