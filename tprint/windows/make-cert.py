"""Make cert.pem + key.pem for T Print's HTTPS (self-signed, 10 years).

Run by windows\\one-click.ps1 only when the T Print folder has no certificate yet.
Covers printatshop.local, localhost, 127.0.0.1 and this PC's current IPv4 addresses.
Usage: venv\\Scripts\\python.exe windows\\make-cert.py <T Print folder>
"""
import datetime
import ipaddress
import socket
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def local_ips():
    ips = {"127.0.0.1"}
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    try:  # the address used to reach the internet (the shop WiFi one)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(i for i in ips if not i.startswith("169.254."))


def main(folder: Path):
    cert_p, key_p = folder / "cert.pem", folder / "key.pem"
    if cert_p.exists() and key_p.exists():
        print("cert.pem and key.pem already exist - kept.")
        return
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "printatshop.local")])
    now = datetime.datetime.now(datetime.timezone.utc)
    alt = [x509.DNSName("printatshop.local"), x509.DNSName("localhost")]
    alt += [x509.IPAddress(ipaddress.ip_address(i)) for i in local_ips()]
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .sign(key, hashes.SHA256()))
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_p.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                        serialization.PrivateFormat.TraditionalOpenSSL,
                                        serialization.NoEncryption()))
    print("Made cert.pem and key.pem for:", ", ".join(str(a.value) for a in alt))


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent)
