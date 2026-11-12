"""Exercise the production certificate preflight with real offline chains."""
from pathlib import Path
import os
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "deploy/huawei/verify_certificate.sh"
pytestmark = pytest.mark.skipif(not shutil.which("openssl"), reason="requires openssl")


def openssl(*args):
    subprocess.run(["openssl", *map(str, args)], check=True, capture_output=True)


@pytest.fixture(scope="module")
def certificate_chains(tmp_path_factory):
    folder = tmp_path_factory.mktemp("certificate-chains")
    root_key, root = folder / "root.key", folder / "root.pem"
    openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", root_key,
            "-out", root, "-subj", "/CN=Test Root", "-days", "2",
            "-addext", "basicConstraints=critical,CA:TRUE",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign")

    def issue(name, issuer, issuer_key, serial, extensions):
        key, csr, cert = (folder / f"{name}.{ext}" for ext in ("key", "csr", "pem"))
        extfile = folder / f"{name}.ext"
        extfile.write_text(extensions, encoding="ascii")
        openssl("req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", key,
                "-out", csr, "-subj", f"/CN={name}")
        openssl("x509", "-req", "-in", csr, "-CA", issuer, "-CAkey", issuer_key,
                "-set_serial", str(serial), "-days", "1", "-extfile", extfile, "-out", cert)
        return key, cert

    ca_ext = "basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n"
    server_ext = ("basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
                  "extendedKeyUsage=serverAuth\nsubjectAltName=DNS:example.org\n")
    intermediate_key, intermediate = issue("Intermediate", root, root_key, 2, ca_ext)
    _, direct = issue("Direct", root, root_key, 3, server_ext)
    _, leaf = issue("Leaf", intermediate, intermediate_key, 4, server_ext)
    _, client = issue("Client", root, root_key, 5, server_ext.replace("serverAuth", "clientAuth"))
    untrusted_key, untrusted_root = folder / "other.key", folder / "other.pem"
    openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", untrusted_key,
            "-out", untrusted_root, "-subj", "/CN=Untrusted", "-days", "1",
            "-addext", "basicConstraints=critical,CA:FALSE",
            "-addext", "extendedKeyUsage=serverAuth")
    chain = folder / "fullchain.pem"
    chain.write_bytes(leaf.read_bytes() + intermediate.read_bytes())
    return {"root": root, "direct": direct, "chain": chain, "missing_intermediate": leaf,
            "client": client, "untrusted": untrusted_root}


@pytest.mark.parametrize("case,expected", [
    ("direct", 0), ("chain", 0), ("missing_intermediate", 1),
    ("client", 1), ("untrusted", 1),
])
def test_certificate_trust_and_server_purpose(certificate_chains, case, expected):
    env = {**os.environ, "SSL_CERT_FILE": str(certificate_chains["root"]), "SSL_CERT_DIR": "/nonexistent"}
    result = subprocess.run(["bash", str(SCRIPT), str(certificate_chains[case])],
                            env=env, capture_output=True, text=True)
    assert result.returncode == expected, result.stderr
    if expected:
        assert "证书链" in result.stderr
