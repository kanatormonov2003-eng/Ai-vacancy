"""Local HTTPS fixture using Python cryptography.

No external openssl executable is required.
The certificate is generated entirely in Python and includes a localhost
IP SAN for 127.0.0.1.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import os
import shutil
import ssl
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass

    def do_GET(self) -> None:
        body = (
            b"<html><head><title>TLS Fixture</title></head>"
            b"<body>secure catalog contact</body></html>"
        )

        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (
            BrokenPipeError,
            ConnectionResetError,
            ConnectionAbortedError,
        ):
            self.close_connection = True


class HTTPSFixture:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="lh-tls-")
        self.key = os.path.join(self.tmp, "key.pem")
        self.cert = os.path.join(self.tmp, "cert.pem")

        self._create_certificate()

        self.httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            _Handler,
        )
        self.httpd.daemon_threads = True

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(
            certfile=self.cert,
            keyfile=self.key,
        )

        self.httpd.socket = context.wrap_socket(
            self.httpd.socket,
            server_side=True,
        )

        self.thread = threading.Thread(
            target=self.httpd.serve_forever,
            daemon=True,
            name="https-fixture-server",
        )

    def _create_certificate(self) -> None:
        key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )

        subject = issuer = x509.Name(
            [
                x509.NameAttribute(NameOID.COUNTRY_NAME, "KG"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "LeadHunter Test"),
                x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1"),
            ]
        )

        now = dt.datetime.now(dt.timezone.utc)

        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=1))
            .not_valid_after(now + dt.timedelta(days=1))
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.IPAddress(
                            ipaddress.ip_address("127.0.0.1")
                        )
                    ]
                ),
                critical=False,
            )
            .sign(
                private_key=key,
                algorithm=hashes.SHA256(),
            )
        )

        with open(self.key, "wb") as handle:
            handle.write(
                key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )

        with open(self.cert, "wb") as handle:
            handle.write(
                certificate.public_bytes(serialization.Encoding.PEM)
            )

    @property
    def url(self) -> str:
        return f"https://127.0.0.1:{self.httpd.server_address[1]}/"

    def start(self) -> "HTTPSFixture":
        self.thread.start()
        return self

    def stop(self) -> None:
        try:
            self.httpd.shutdown()
            self.httpd.server_close()

            if self.thread.is_alive():
                self.thread.join(timeout=2.0)
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)


if __name__ == "__main__":
    fixture = HTTPSFixture().start()

    print("HTTPS fixture:", fixture.url)
    print("CA/certificate:", fixture.cert)

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        fixture.stop()
