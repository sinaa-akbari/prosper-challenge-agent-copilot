#
# Generate a self-signed cert so the app can be served over HTTPS on the LAN.
#
# Browsers only expose the microphone in a "secure context": https, or http on
# localhost. A phone or laptop hitting http://192.168.x.x:7860 gets neither, so
# `navigator.mediaDevices` is undefined and test calls die before they start.
# Serving TLS — even with a cert nobody trusts — fixes that. The visitor clicks
# through the warning once and the origin counts as secure from then on.
#
#   python make_cert.py      then restart the server; it picks the cert up
#
# The cert covers localhost plus every LAN address this machine currently has,
# so it keeps working whether you reach the app by name or by IP. Re-run it if
# your DHCP lease changes and the address moves.
#

import datetime
import ipaddress
import socket
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

CERT_DIR = Path(__file__).resolve().parent / "certs"
CERT_FILE = CERT_DIR / "dev.crt"
KEY_FILE = CERT_DIR / "dev.key"

DAYS = 397


def local_ips() -> list[str]:
    """Every IPv4 address this host answers on, best effort."""
    found = {"127.0.0.1"}

    try:
        _, _, addrs = socket.gethostbyname_ex(socket.gethostname())
        found.update(addrs)
    except OSError:
        pass

    # The routable address isn't always the first one the hostname resolves to;
    # a UDP socket to an off-box address reveals which interface actually wins.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        found.add(sock.getsockname()[0])
    except OSError:
        pass
    finally:
        sock.close()

    return sorted(found)


def build_sans() -> list[x509.GeneralName]:
    hostname = socket.gethostname()
    names: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.DNSName(hostname),
        x509.DNSName(f"{hostname}.local"),
        x509.IPAddress(ipaddress.IPv6Address("::1")),
    ]
    names.extend(x509.IPAddress(ipaddress.IPv4Address(ip)) for ip in local_ips())
    return names


def main() -> None:
    CERT_DIR.mkdir(exist_ok=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Prosper Agent Composer (dev)")])
    now = datetime.datetime.now(datetime.timezone.utc)
    sans = build_sans()

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))  # tolerate clock skew
        .not_valid_after(now + datetime.timedelta(days=DAYS))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.ObjectIdentifier("1.3.6.1.5.5.7.3.1")]),  # serverAuth
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    KEY_FILE.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    print(f"cert: {CERT_FILE}")
    print(f"key:  {KEY_FILE}")
    print(f"valid {DAYS} days, covering:")
    for san in sans:
        print(f"  - {san.value}")


if __name__ == "__main__":
    main()
