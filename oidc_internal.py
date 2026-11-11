"""Optional direct Docker-network transport for Authentik OIDC HTTP requests.

Keep the OIDC issuer, authorization endpoint and browser redirects on the public
HTTPS hostname. Only server-to-server HTTP requests are routed to the internal
address from OIDC_DISCOVERY_URL when OIDC_HOST is configured.
"""

import re
from urllib.parse import urlsplit, urlunsplit


def validate_oidc_host(host):
    """OIDC_HOST is a DNS hostname, never a URL, IP:port, or header payload."""
    if not host or len(host) > 253 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', host):
        raise ValueError('OIDC_HOST must be a hostname such as tik.esquema.qzz.io')
    if any(not label or len(label) > 63 or label.startswith('-') or label.endswith('-')
           for label in host.split('.')):
        raise ValueError('OIDC_HOST contains an invalid DNS label')
    return host.lower()


def oidc_backend_request(url, discovery_url, host):
    """Get the actual URL and trusted headers for an OIDC backend request.

    Only the configured internal discovery origin or the matching public HTTPS
    OIDC_HOST can be routed. All other URLs are left completely unchanged.
    """
    if not host:
        return url, {}

    internal = urlsplit(discovery_url)
    request = urlsplit(url)
    internal_origin = (internal.scheme, internal.netloc)
    request_origin = (request.scheme, request.netloc)

    if request.scheme == 'https' and request.netloc.lower() == host:
        url = urlunsplit((internal.scheme, internal.netloc, request.path,
                          request.query, request.fragment))
    elif request_origin != internal_origin:
        return url, {}

    return url, {
        'Host': host,
        'X-Forwarded-Host': host,
        'X-Forwarded-Proto': 'https',
    }
