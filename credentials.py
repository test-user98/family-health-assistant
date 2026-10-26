"""Load one authorized provider credential without persisting or logging secrets."""
import json
import os
import socket
import urllib.error
import urllib.request


class CredentialError(RuntimeError):
    """A sanitized credential retrieval failure."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, *args, **kwargs):
        raise CredentialError('Doppler returned an unexpected redirect.')


def openai_key_from_doppler(token, *, opener=None, timeout=15):
    """Retrieve only OPENAI_API_KEY from the service token's scoped configuration."""
    if not isinstance(token, str) or not token.strip():
        raise CredentialError('A Doppler service token is required.')
    request = urllib.request.Request(
        'https://api.doppler.com/v3/configs/config/secret?name=OPENAI_API_KEY',
        headers={'Authorization': 'Bearer ' + token.strip(), 'Accept': 'application/json'},
    )
    try:
        open_request = opener or urllib.request.build_opener(_NoRedirect()).open
        with open_request(request, timeout=timeout) as response:
            raw = response.read(100001)
        if len(raw) > 100000:
            raise CredentialError('Doppler returned an oversized response.')
        payload = json.loads(raw)
        if payload.get('name', 'OPENAI_API_KEY') != 'OPENAI_API_KEY':
            raise CredentialError('Doppler returned an unexpected secret name.')
        value = payload.get('value', {}).get('computed')
        if not isinstance(value, str) or not value.strip():
            raise CredentialError('OPENAI_API_KEY was not returned by Doppler.')
        return value.strip()
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        raise CredentialError(f'Doppler rejected the request (HTTP {status}).') from None
    except urllib.error.URLError as error:
        if isinstance(error.reason, socket.gaierror):
            raise CredentialError('Could not resolve api.doppler.com. Run from a terminal with network access; no credential was retrieved.') from None
        raise CredentialError('Could not connect to Doppler within the request timeout.') from None
    except (TimeoutError, OSError):
        raise CredentialError('Could not connect to Doppler within the request timeout.') from None
    except (ValueError, TypeError, AttributeError):
        raise CredentialError('Doppler returned an invalid credential response.') from None


def load_openai_key(api_key=None, doppler_token=None, *, opener=None):
    """Prefer an explicit/environment key, then the explicitly scoped Doppler token."""
    key = api_key or os.environ.get('OPENAI_API_KEY')
    if key:
        return key
    token = doppler_token or os.environ.get('DOPPLER_TOKEN')
    return openai_key_from_doppler(token, opener=opener) if token else None
