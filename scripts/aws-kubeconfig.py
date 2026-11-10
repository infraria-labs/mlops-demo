"""Retrieve cluster-admin kubeconfig through an interactive SSM session.

Capture certificates privately, without SSM Run Command output/log storage.
Requires AWS_PROFILE, Terraform outputs and the Session Manager plugin.
"""
import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    outputs = json.loads(subprocess.check_output(
        ["terraform", "-chdir=infra/terraform", "output", "-json"], cwd=ROOT))
    master, slave = pty.openpty()
    process = subprocess.Popen([
        "aws", "ssm", "start-session", "--region", outputs["region"]["value"],
        "--target", outputs["instance_id"]["value"],
    ], stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
    os.close(slave)
    content = b""
    sent = False
    try:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if select.select([master], [], [], 1)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                content += chunk
                if not sent and re.search(rb"sh-[0-9.]+\$", content):
                    command = "printf '\\n__KCFG_START__\\n'; sudo cat /etc/rancher/k3s/k3s.yaml; printf '\\n__KCFG_END__\\n'\n"
                    os.write(master, command.encode())
                    sent = True
                clean = content.replace(b"\r", b"")
                match = re.search(rb"\n__KCFG_START__\n(.*?)\n__KCFG_END__\n", clean, re.S)
                if match:
                    config = match[1].decode()
                    if "client-key-data:" not in config or not re.search(r"server: https://(?:127\.0\.0\.1|0\.0\.0\.0):6443", config):
                        raise RuntimeError("Unexpected kubeconfig; no credentials printed")
                    config = config.replace("server: https://0.0.0.0:6443", "server: https://127.0.0.1:6443")
                    directory = ROOT / ".aws-k3s"
                    directory.mkdir(mode=0o700, exist_ok=True)
                    path = directory / "kubeconfig"
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    os.fchmod(fd, 0o600)
                    with os.fdopen(fd, "w") as stream:
                        stream.write(config)
                    os.write(master, b"exit\n")
                    print("Saved private kubeconfig to .aws-k3s/kubeconfig")
                    return
                if process.poll() is not None:
                    break
        raise RuntimeError("Could not retrieve kubeconfig within 180s; inspect node/bootstrap via SSM")
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        process.wait(timeout=10)
        os.close(master)


if __name__ == "__main__":
    main()
