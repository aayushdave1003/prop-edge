"""Notifications — email push + a shared slate text formatter.

Discord already gets a rich embed; this adds an **email** push of the morning
recommended slate (free, reliable, shows as a phone push via your mail app) and
one canonical text format reused by the email body and the dashboard's "tail
this slate" copy box, so the slate reads the same everywhere.

Setup (one-time, user): set SMTP_USER + SMTP_PASSWORD (for Gmail, an App
Password — accounts with 2FA can't use the real password) and EMAIL_TO (defaults
to SMTP_USER). Defaults target Gmail; override SMTP_HOST/PORT for another
provider. Unset → silently skipped.
"""
import smtplib
from email.message import EmailMessage

from props.utils.config import settings
from props.utils.logging import log

_STAT_LABEL = {
    "points": "pts", "rebounds": "reb", "assists": "ast", "threes_made": "3pm",
    "pts_rebs_asts": "PRA", "pts_rebs": "P+R", "pts_asts": "P+A", "rebs_asts": "R+A",
    "strikeouts_pitcher": "Ks", "hits": "hits", "total_bases": "TB", "rbis": "RBI",
    "home_runs": "HR", "goals": "goals", "saves": "saves",
}
_SPORT_EMOJI = {"nba": "🏀", "wnba": "🏀", "mlb": "⚾", "nhl": "🏒"}


def format_slate(picks: list[dict], parlay: list[dict] | None = None,
                 date_label: str | None = None) -> str:
    """Render the recommended slate as clean, copyable plain text.

    picks / parlay rows: {sport, player, direction, line, stat, prob} (prob is the
    shown win probability, 0–1). Used for the email body and the dashboard's
    one-click "tail" box."""
    head = f"⚡ prop-edge recommended{(' — ' + date_label) if date_label else ''}"
    lines = [head, ""]
    for p in picks:
        emoji = _SPORT_EMOJI.get(p.get("sport", ""), "•")
        stat = _STAT_LABEL.get(p["stat"], p["stat"])
        line = f"{float(p['line']):g}"
        prob = f" — {round(p['prob'] * 100)}%" if p.get("prob") is not None else ""
        lines.append(f"{emoji} {p['player']}: {p['direction'].upper()} {line} {stat}{prob}")
    if parlay and len(parlay) >= 2:
        joint = 1.0
        for p in parlay:
            joint *= (p.get("prob") or 0)
        names = " + ".join(p["player"] for p in parlay)
        lines += ["", f"⭐ Best {len(parlay)}-pick: {names} — {round(joint * 100)}% joint"]
    lines += ["", "paper-tracking only · not betting advice"]
    return "\n".join(lines)


def send_email(subject: str, body: str) -> bool:
    """Send a plain-text email via SMTP. No-op (returns False) when SMTP isn't
    configured or the send fails — never raises into the caller."""
    user = getattr(settings, "smtp_user", "") or ""
    password = getattr(settings, "smtp_password", "") or ""
    if not user or not password:
        return False
    to_addr = (getattr(settings, "email_to", "") or "") or user
    host = getattr(settings, "smtp_host", "") or "smtp.gmail.com"
    port = int(getattr(settings, "smtp_port", 587) or 587)
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = user
        msg["To"] = to_addr
        msg.set_content(body)
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.starttls()
            s.login(user, password)
            s.send_message(msg)
        log.info("email_sent", to=to_addr)
        return True
    except Exception as e:
        log.warning("email_failed", error=str(e)[:140])
        return False
