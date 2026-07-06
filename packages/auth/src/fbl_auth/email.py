"""Email delivery via Azure Communication Services (§8.10, Distribution §4).

Three German, on-brand messages drive the double-opt-in flow:
* **send_verify** – the double-opt-in confirmation link,
* **send_key** – the API-key delivery (shown once),
* **send_token** – legacy single-step delivery (kept for the original ``accounts.signup``).

Delivery is an injected capability (``EmailSender``) so the account logic stays pure and
unit-testable: ``AcsEmailSender`` in Azure, ``NullEmailSender`` (logs, sends nothing) in
local/dev/tests.
"""

from __future__ import annotations

import html
import logging
from typing import Protocol

from fbl_core.config import Settings

logger = logging.getLogger(__name__)

_BRAND = "Agentic-Firmenbuch.at"
_SUPPORT = "office@jngb.online"
_MCP_URL = "https://mcp.agentic-firmenbuch.at/mcp"
_COWORK_GUIDE = "https://www.agentic-firmenbuch.at/cowork.html"


# --- message bodies (German; HTML + plaintext) ---------------------------------------------


def _verify_subject() -> str:
    return f"Bitte bestätige deine E-Mail – {_BRAND}"


def _verify_text(verify_url: str) -> str:
    return (
        f"Willkommen bei {_BRAND}.\n\n"
        "Bitte bestätige deine E-Mail-Adresse, um deinen kostenlosen API-Key "
        "zu erhalten:\n\n"
        f"    {verify_url}\n\n"
        "Hinweis: Pro E-Mail-Adresse ist immer nur EIN Key gültig. Falls du bereits einen Key "
        "hast, wird er mit der Bestätigung ersetzt (der alte wird ungültig).\n\n"
        "Der Link ist 24 Stunden gültig. Wenn du das nicht angefordert hast, ignoriere diese "
        "E-Mail einfach.\n"
    )


def _key_subject() -> str:
    return f"Dein API-Key – {_BRAND}"


def _code_cmd(api_key: str) -> str:
    return (
        "claude mcp add --scope user --transport http agentic-firmenbuch "
        'https://mcp.agentic-firmenbuch.at/mcp --header "X-API-Key: ' + api_key + '"'
    )


def _copilot_cmd(api_key: str) -> str:
    return (
        'code --add-mcp "{\\"name\\":\\"agentic-firmenbuch\\",\\"type\\":\\"http\\",'
        '\\"url\\":\\"https://mcp.agentic-firmenbuch.at/mcp\\",'
        '\\"headers\\":{\\"X-API-Key\\":\\"' + api_key + '\\"}}"'
    )


def _key_text(api_key: str, *, onboarding_url: str) -> str:
    return (
        f"Willkommen bei {_BRAND}. Dein Zugang ist bereit.\n\n"
        "── Am einfachsten: Claude Cowork / Claude Desktop (kein Key nötig) ──\n"
        "Per Klick verbinden über einen Connector mit einmaliger E-Mail-Anmeldung –\n"
        "diesen API-Key brauchst du dafür NICHT. In Claude:\n"
        "Einstellungen → Konnektoren → Connector hinzufügen mit der Adresse:\n"
        f"  {_MCP_URL}\n"
        f"Bebilderte Schritt-für-Schritt-Anleitung: {_COWORK_GUIDE}\n\n"
        "── Mit API-Key: Claude Code, VS Code, Cursor & andere ──\n"
        "Dein API-Key (wird nur einmal angezeigt – bitte sicher aufbewahren):\n\n"
        f"    {api_key}\n\n"
        "Einfach den passenden Befehl ins Terminal kopieren (Key ist schon eingesetzt):\n\n"
        "  A) Claude Code (Terminal, macOS / Windows / Linux)\n"
        f"     {_code_cmd(api_key)}\n\n"
        "  B) VS Code (GitHub Copilot)\n"
        f"     {_copilot_cmd(api_key)}\n\n"
        f"Volle Anleitung für alle Clients (Cursor, …): {onboarding_url}\n\n"
        "Testen: in einem neuen Chat fragen „Bist du mit dem Agentic-Firmenbuch-Server "
        'verbunden, welche Werkzeuge hast du?". Nennt der Agent die Firmenbuch-Werkzeuge, '
        "ist alles bereit.\n\n"
        f"Fragen oder Probleme? Schreib einfach an {_SUPPORT} – wir helfen gern.\n\n"
        "Neuen Key brauchen? Jederzeit auf der Website anfordern.\n\n"
        "Mit der Nutzung des Dienstes (Verbinden mit dem MCP-Server) gelten die Allgemeinen "
        "Geschäftsbedingungen: https://www.agentic-firmenbuch.at/nutzungsbedingungen.html "
        "– Die Nutzung des Services erfolgt unter Ausschluss jeglicher Gewährleistung.\n"
    )


# Shared visual style for all HTML emails (corporate: dark header bar + green accent).
_BOX = (
    "font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;background:#0d0f14;"
    "color:#cfe9dc;border:1px solid #1f2430;border-radius:10px;padding:14px 16px;margin:8px 0 0;"
    "white-space:pre-wrap;word-break:break-all;line-height:1.55"
)
_BTN = (
    "display:inline-block;background:#19C37D;color:#08130D;text-decoration:none;font-weight:700;"
    "padding:12px 20px;border-radius:10px;margin:4px 0"
)


def _shell(inner: str) -> str:
    """Wrap email content in the branded corporate layout (dark header, green accent, footer)."""
    return (
        '<div style="background:#f4f5f7;padding:24px 12px;'
        'font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">'
        '<div style="max-width:580px;margin:0 auto;background:#fff;border-radius:14px;'
        'overflow:hidden;border:1px solid #e6e8eb">'
        '<div style="background:#0A0B0E;padding:18px 28px">'
        '<span style="color:#fff;font-size:17px;font-weight:700;letter-spacing:-.01em">'
        '<span style="color:#19C37D">Agentic</span>-Firmenbuch.at</span></div>'
        f'<div style="padding:26px 28px;color:#1a1d22;font-size:15px;line-height:1.6">{inner}</div>'
        '<div style="padding:16px 28px;background:#fafbfc;border-top:1px solid #eee;'
        'color:#8a909a;font-size:12px;line-height:1.5">'
        "Mit der Nutzung des Dienstes (Verbinden mit dem MCP-Server) gelten die "
        '<a href="https://www.agentic-firmenbuch.at/nutzungsbedingungen.html" '
        'style="color:#0f9d63">Allgemeinen Geschäftsbedingungen</a> – Nutzung '
        "unter Ausschluss jeglicher Gewährleistung.<br>"
        "Österreichisches Firmenbuch / BMJ – Justiz (CC BY 4.0) · agentic-firmenbuch.at</div>"
        "</div></div>"
    )


def _verify_html(verify_url: str) -> str:
    """Branded HTML confirmation email (same style as the key email)."""
    inner = (
        f"<p>Willkommen bei <strong>{_BRAND}</strong>.</p>"
        "<p>Bestätige deine E-Mail-Adresse, dann bekommst du deinen kostenlosen API-Key:</p>"
        f'<p style="margin:18px 0"><a href="{verify_url}" target="_blank" rel="noopener" '
        f'style="{_BTN}">E-Mail bestätigen</a></p>'
        '<p style="font-size:13px;color:#6b7280">Falls der Button nicht funktioniert, diesen Link '
        f'öffnen:<br><a href="{verify_url}" target="_blank" rel="noopener" '
        'style="color:#0f9d63;word-break:break-all">'
        f"{verify_url}</a></p>"
        '<p style="font-size:13px;color:#6b7280">Pro E-Mail-Adresse gilt immer nur EIN Key (ein '
        "neuer ersetzt den alten). Der Link ist 24 Stunden gültig. Nicht angefordert? Einfach "
        "ignorieren.</p>"
    )
    return _shell(inner)


def _key_html(api_key: str, *, onboarding_url: str) -> str:
    """Branded HTML key email: two paste-and-go boxes (Claude Code + VS Code) + honest
    Cowork note (that client connects by OAuth — link to the illustrated guide)."""
    lbl = "margin:24px 0 4px;font-size:13px;font-weight:700;color:#0A0B0E"
    code = html.escape(_code_cmd(api_key))
    copilot = html.escape(_copilot_cmd(api_key))
    soon = (
        "background:#eafaf2;border:1px solid #bfe6d2;border-radius:10px;"
        "padding:12px 14px;margin:16px 0 0;color:#11503a;font-size:13.5px"
    )
    inner = (
        f"<p>Willkommen bei <strong>{_BRAND}</strong>. Dein Zugang ist bereit.</p>"
        f'<div style="{soon}"><strong>Am einfachsten: Claude Cowork / Claude Desktop – '
        "kein Key nötig.</strong><br>Per Klick verbinden über einen Connector mit einmaliger "
        "E-Mail-Anmeldung. In Claude: <strong>Einstellungen → Konnektoren</strong> → Connector "
        f"hinzufügen mit <code>{_MCP_URL}</code>. "
        f'<a href="{_COWORK_GUIDE}" target="_blank" rel="noopener" style="color:#0f9d63">'
        "Bebilderte Anleitung ansehen</a>.</div>"
        f'<p style="{lbl}">Mit API-Key: Claude Code, VS Code, Cursor &amp; andere</p>'
        '<p style="margin:0 0 4px;font-size:13.5px;color:#6b7280">Dein API-Key '
        "(nur einmal angezeigt, bitte sicher aufbewahren):</p>"
        f'<div style="{_BOX}">{api_key}</div>'
        '<p style="margin:20px 0 0;font-size:13.5px">Passenden Befehl ins Terminal kopieren, '
        "der Key ist schon eingesetzt:</p>"
        f'<p style="{lbl}">A) Claude Code (Terminal, macOS / Windows / Linux)</p>'
        f'<div style="{_BOX}">{code}</div>'
        f'<p style="{lbl}">B) VS Code (GitHub Copilot)</p>'
        f'<div style="{_BOX}">{copilot}</div>'
        '<p style="margin:18px 0 0;font-size:13.5px">Anderes Tool (Cursor, …)? '
        f'<a href="{onboarding_url}" target="_blank" rel="noopener" style="color:#0f9d63">'
        "Volle Anleitung</a></p>"
        '<p style="margin:22px 0 0"><strong>Testen</strong>: in einem neuen Chat fragen '
        "&bdquo;Bist du mit dem Agentic-Firmenbuch-Server verbunden, welche Werkzeuge hast "
        "du?&ldquo;. Nennt der Agent die Firmenbuch-Werkzeuge, ist alles bereit.</p>"
        '<p style="margin:20px 0 0;font-size:13.5px;color:#374151;background:#f3f4f6;'
        'border-radius:8px;padding:11px 13px"><strong>Fragen oder Probleme?</strong> '
        f'Schreib einfach an <a href="mailto:{_SUPPORT}" style="color:#0f9d63">{_SUPPORT}</a> '
        "– wir helfen gern.</p>"
        '<p style="color:#6b7280;font-size:13px;margin-top:20px">Neuen Key brauchen? Jederzeit '
        "auf der Website anfordern.</p>"
    )
    return _shell(inner)


def _oauth_login_subject() -> str:
    return f"Verbinden bestätigen – {_BRAND}"


def _oauth_login_text(login_url: str, client_name: str) -> str:
    return (
        f"Du verbindest {client_name or 'ein KI-Tool'} mit {_BRAND}.\n\n"
        "Klicke den Link, um die Verbindung zu bestätigen (kein Key nötig):\n\n"
        f"    {login_url}\n\n"
        "Verbindung testen: Öffne danach einen neuen Chat und frage z. B. "
        '"Bist du mit Agentic-Firmenbuch.at verbunden? Was kannst du abfragen?". Nennt der '
        "Agent die Firmenbuch-Werkzeuge, ist alles bereit.\n\n"
        "Der Link ist 15 Minuten gültig. Wenn du das nicht angefordert hast, ignoriere "
        "diese E-Mail einfach.\n"
    )


def _oauth_login_html(login_url: str, client_name: str) -> str:
    safe_client = html.escape(client_name or "ein KI-Tool")
    inner = (
        f"<p>Du verbindest <strong>{safe_client}</strong> mit <strong>{_BRAND}</strong>.</p>"
        "<p>Klicke zum Bestätigen – danach ist die Verbindung aktiv, ganz ohne Key:</p>"
        f'<p style="margin:18px 0"><a href="{login_url}" target="_blank" rel="noopener" '
        f'style="{_BTN}">Verbindung bestätigen</a></p>'
        '<p style="font-size:13px;color:#6b7280">Falls der Button nicht funktioniert, diesen '
        f'Link öffnen:<br><a href="{login_url}" target="_blank" rel="noopener" '
        'style="color:#0f9d63;word-break:break-all">'
        f"{login_url}</a></p>"
        '<p style="font-size:13px;color:#374151;background:#f3f4f6;border-radius:8px;'
        'padding:10px 12px"><strong>Verbindung testen:</strong> Öffne danach einen neuen Chat '
        'und frage z. B. <em>"Bist du mit Agentic-Firmenbuch.at verbunden? Was kannst du '
        'abfragen?"</em>. Nennt der Agent die Firmenbuch-Werkzeuge, ist alles bereit.</p>'
        '<p style="font-size:13px;color:#6b7280">Der Link ist 15 Minuten gültig. Nicht '
        "angefordert? Einfach ignorieren.</p>"
    )
    return _shell(inner)


def _legacy_subject() -> str:
    return f"Dein API-Key – {_BRAND}"


def _legacy_text(token: str) -> str:
    return (
        f"Willkommen bei {_BRAND}.\n\n"
        f"Dein API-Key (wird nur einmal angezeigt – bitte sicher aufbewahren):\n\n    {token}\n\n"
        'Übergib ihn als Header "X-API-Key" an den MCP-Server.\n'
    )


class EmailSender(Protocol):
    """Delivers the signup/verify/key emails to an address. Returns True iff accepted."""

    def send_verify(self, to: str, verify_url: str) -> bool: ...

    def send_key(self, to: str, api_key: str) -> bool: ...

    def send_oauth_login(self, to: str, login_url: str, client_name: str) -> bool: ...

    def send_token(self, to: str, token: str) -> bool:  # legacy single-step path
        ...

    def send_alert(self, to: str, subject: str, text: str) -> bool:  # ops/monitoring alert
        ...


class NullEmailSender:
    """No-op sender for local/dev/tests – records that delivery was skipped."""

    def send_alert(self, to: str, subject: str, text: str) -> bool:
        logger.warning("ACS not configured; ALERT not emailed to %s: %s", to, subject)
        return False

    def send_verify(self, to: str, verify_url: str) -> bool:
        logger.info("ACS not configured; skipping verify email to %s (%s)", to, verify_url)
        return False

    def send_key(self, to: str, api_key: str) -> bool:
        logger.info("ACS not configured; skipping key email to %s", to)
        return False

    def send_oauth_login(self, to: str, login_url: str, client_name: str) -> bool:
        logger.info("ACS not configured; skipping oauth-login email to %s (%s)", to, login_url)
        return False

    def send_token(self, to: str, token: str) -> bool:
        logger.info("ACS not configured; skipping token email to %s", to)
        return False


class AcsEmailSender:
    """Sends mail via Azure Communication Services (lazy SDK import)."""

    def __init__(
        self,
        connection_string: str,
        sender_address: str,
        *,
        site_base_url: str = "https://agentic-firmenbuch.at",
    ) -> None:
        self._connection_string = connection_string
        self._sender_address = sender_address
        self._site = site_base_url.rstrip("/")

    def _send(self, to: str, subject: str, text: str, html: str | None = None) -> bool:
        from azure.communication.email import EmailClient

        client = EmailClient.from_connection_string(self._connection_string)
        content: dict[str, str] = {"subject": subject, "plainText": text}
        if html is not None:
            content["html"] = html  # clients that render HTML get the formatted version
        message = {
            "senderAddress": self._sender_address,
            "recipients": {"to": [{"address": to}]},
            "content": content,
        }
        # Submit the message (raises on a real submission error) but DON'T poll the
        # long-running send to completion – `.result()` blocked the HTTP response for several
        # seconds ("Wird gesendet …" felt stuck). ACS delivers asynchronously after submission.
        client.begin_send(message)
        return True

    def send_verify(self, to: str, verify_url: str) -> bool:
        return self._send(
            to, _verify_subject(), _verify_text(verify_url), html=_verify_html(verify_url)
        )

    def send_key(self, to: str, api_key: str) -> bool:
        url = f"{self._site}/onboarding.html"
        return self._send(
            to,
            _key_subject(),
            _key_text(api_key, onboarding_url=url),
            html=_key_html(api_key, onboarding_url=url),
        )

    def send_oauth_login(self, to: str, login_url: str, client_name: str) -> bool:
        return self._send(
            to,
            _oauth_login_subject(),
            _oauth_login_text(login_url, client_name),
            html=_oauth_login_html(login_url, client_name),
        )

    def send_token(self, to: str, token: str) -> bool:
        return self._send(to, _legacy_subject(), _legacy_text(token))

    def send_alert(self, to: str, subject: str, text: str) -> bool:
        """Plain-text ops alert (pipeline anomaly) to the operator — no HTML."""
        return self._send(to, subject, text)


def email_sender_from_settings(settings: Settings) -> EmailSender:
    """Build the ACS sender when fully configured, else the no-op sender."""
    if settings.acs_connection_string and settings.acs_sender_address:
        return AcsEmailSender(
            settings.acs_connection_string,
            settings.acs_sender_address,
            site_base_url=settings.site_base_url,
        )
    return NullEmailSender()
