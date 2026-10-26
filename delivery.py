"""Explicit email authorization and evidence ceilings. No connector or ambient credentials.

Adapters are disabled unless constructed by trusted application code with authorization.
SMTP acceptance is not inbox receipt; SMTP timeouts are outcome_unknown, never auto-retried.
"""
import hashlib
import hmac
import json
import re
import smtplib
from email.message import EmailMessage
from urllib.parse import urlsplit


class DeliveryError(Exception):
    code = 'delivery_error'
class NotAuthorized(DeliveryError):
    code = 'not_authorized'
class OutcomeUnknown(DeliveryError):
    code = 'outcome_unknown'
class RetryableBeforeSend(DeliveryError):
    code = 'retryable_before_send'
class PermanentFailure(DeliveryError):
    code = 'permanent_failure'


def address(value):
    if not isinstance(value, str) or not re.fullmatch(r'[^\s<>@,;\r\n]+@[^\s<>@,;\r\n]+\.[^\s<>@,;\r\n]+', value):
        raise NotAuthorized('An explicitly authorized single email address is required.')
    return value.lower()


class EmailAuthorization:
    def __init__(self, *, enabled=False, household=None, sender=None, recipient=None, consent=False):
        if not enabled or not consent or not household:
            raise NotAuthorized('Email is disabled or lacks explicit sender/recipient consent.')
        self.household, self.sender, self.recipient = household, address(sender), address(recipient)

    def check(self, household):
        if household != self.household:
            raise NotAuthorized('Email authorization does not cover this household.')


class SMTPAdapter:
    """Injected SMTP session factory; no credential lookup/storage or connection at init."""
    supports_reconciliation = False
    supports_idempotency = False

    def __init__(self, authorization, session_factory, *, test_mode=False):
        self.authorization, self.session_factory = authorization, session_factory
        self.test_mode = test_mode

    def send(self, household, key):
        self.authorization.check(household)
        message = EmailMessage()
        message['From'], message['To'] = self.authorization.sender, self.authorization.recipient
        message['Subject'] = 'Your reminder'
        message['Message-ID'] = '<' + hashlib.sha256(key.encode()).hexdigest() + '@local-reminder.invalid>'
        # No symptoms, prescriptions, patient name, record links or event text leave the app.
        message.set_content('A reminder you requested is due. Open your local app to review its details.')
        try:
            session = self.session_factory()  # Trusted factory must configure secure transport itself.
        except Exception:
            raise RetryableBeforeSend('Connection failed before any message was submitted.') from None
        try:
            refused = session.send_message(message)
            if refused:
                raise PermanentFailure('The recipient was not accepted.')
            return {'kind': 'provider_accepted', 'provider_id': str(message['Message-ID']),
                    'origin': 'mock' if self.test_mode else 'provider'}
        except DeliveryError:
            raise
        except Exception:
            raise OutcomeUnknown('Submission outcome is unknown; reconciliation is required before retry.') from None
        finally:
            try:
                session.quit()
            except Exception:
                pass


def verify_webhook(body, signature, secret):
    """Called only by a separately configured authenticated provider integration."""
    if not isinstance(body, bytes) or len(body) > 65536 or not isinstance(secret, bytes) or not secret:
        raise NotAuthorized('Webhook authentication is required.')
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        raise NotAuthorized('Webhook authentication failed.')
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        raise PermanentFailure('Malformed delivery event.') from None
    return data


def validated_mailbox_link(url, *, observed_account, authorized_account, verified=False):
    if not verified or not authorized_account or observed_account != authorized_account:
        raise NotAuthorized('No authorized mailbox observation supports this link.')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in ('mail.google.com', 'outlook.office.com', 'outlook.live.com')
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or any(c in url for c in '\r\n')):
        raise NotAuthorized('No supported mailbox link is available.')
    return url


def evidence_summary(events):
    """Order-independent evidence reducer; a transport ID never becomes a mailbox URL."""
    kinds = {x['kind'] for x in events}
    prefix = 'Test evidence only: ' if any(x.get('origin') == 'mock' for x in events) else ''
    if 'mailbox_observed' in kinds:
        return prefix + 'Observed in the authorized mailbox; reading by a person is unconfirmed.'
    if 'bounced' in kinds:
        return prefix + 'A delivery failure was reported. Inbox receipt is unconfirmed.'
    if 'receiving_server_delivered' in kinds:
        return prefix + 'The receiving server accepted it; inbox placement and reading are unconfirmed.'
    if 'provider_accepted' in kinds:
        return prefix + 'The provider accepted it for delivery; inbox receipt is unconfirmed.'
    if 'outcome_unknown' in kinds:
        return prefix + 'The submission outcome is unknown. Reconcile before retrying.'
    if 'local_recorded' in kinds:
        return 'Recorded locally in the SQLite sink; not emailed.'
    if 'expired' in kinds:
        return 'No dispatch was recorded before expiry. No email or receipt is confirmed.'
    return 'No delivery is confirmed.'
