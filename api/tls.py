"""Local TLS for the control API (build step 1.15).

The API listens on 127.0.0.1 only and always over HTTPS. On first run a self-signed certificate
(ECDSA P-256, valid two years) is generated for ``localhost`` / ``127.0.0.1``. The desktop app
pins the certificate's SHA-256 fingerprint instead of trusting any certificate, so another local
process cannot impersonate the engine. Phone apps (Phase 8) reach the server through a private
VPN with a CA-issued certificate (step 7.06).
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

VALIDITY: Final = timedelta(days=730)
RENEW_BEFORE: Final = timedelta(days=30)


@dataclass(frozen=True)
class TlsFiles:
    cert: Path
    key: Path
    fingerprint: str  # SHA-256 of the DER certificate, hex


def spki_pin(cert_pem: bytes) -> str:
    """Base64 SHA-256 of the certificate's public key (SubjectPublicKeyInfo).

    Chromium-based webviews (Edge WebView2 on Windows) accept this as
    ``--ignore-certificate-errors-spki-list``: only a certificate with exactly this key is
    trusted, i.e. the desktop app pins the engine's own certificate.
    """
    cert = x509.load_pem_x509_certificate(cert_pem)
    spki = cert.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return base64.b64encode(hashlib.sha256(spki).digest()).decode()


def fingerprint(cert_pem: bytes) -> str:
    cert = x509.load_pem_x509_certificate(cert_pem)
    return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def ensure_certificate(directory: Path) -> TlsFiles:
    """Return the certificate, creating or renewing it when missing or near expiry."""
    cert_path, key_path = directory / "api-cert.pem", directory / "api-key.pem"
    if cert_path.exists() and key_path.exists():
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        if cert.not_valid_after_utc - datetime.now(UTC) > RENEW_BEFORE:
            return TlsFiles(cert_path, key_path, fingerprint(cert_path.read_bytes()))
    directory.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TradingSystem local API")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + VALIDITY)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    # Key file readable by the current user only (on Windows the per-user profile folder
    # already restricts access).
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key_pem)
    cert_path.write_bytes(cert_pem)
    return TlsFiles(cert_path, key_path, fingerprint(cert_pem))
