#!/usr/bin/env python3
"""Disposable real OpenSSH fixture; no host SSH configuration or keys are used."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
DEFAULT_IMAGE = "localhost/harness-ssh-fixture:0.5.0"
PAYLOAD = b"fixture revision one\n"
TARGET = "/srv/site/VERSION"

# Executed only inside the isolated container. Private keys never leave it.
INITIALIZE = r'''
import os,pwd,subprocess
from pathlib import Path
for path in ('/run/sshd','/root/.ssh','/srv/site/releases','/tmp/tree'):
    Path(path).mkdir(parents=True,exist_ok=True)
os.chmod('/root/.ssh',0o700)
for name,key in (('host','/tmp/host-key'),('deploy','/root/.ssh/id_ed25519'),('observer','/tmp/observer-key')):
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',key],check=True)
    if name != 'host':
        account=pwd.getpwnam(name)
        folder=Path('/home')/name/'.ssh';folder.mkdir(mode=0o700)
        auth=folder/'authorized_keys';auth.write_bytes(Path(key+'.pub').read_bytes())
        os.chmod(auth,0o600);os.chown(folder,account.pw_uid,account.pw_gid);os.chown(auth,account.pw_uid,account.pw_gid)
host=Path('/tmp/host-key.pub').read_text().split()
Path('/root/.ssh/known_hosts').write_text('127.0.0.1 '+host[0]+' '+host[1]+'\n')
os.chmod('/root/.ssh/known_hosts',0o600)
deploy=pwd.getpwnam('deploy')
for path in ('/srv/site','/srv/site/releases'):
    os.chown(path,deploy.pw_uid,deploy.pw_gid);os.chmod(path,0o755)
for path,data,mode in (('/srv/site/VERSION',b'fixture revision zero\n',0o644),
                       ('/srv/site/worker-only',b'restricted fixture evidence\n',0o600)):
    Path(path).write_bytes(data);os.chmod(path,mode);os.chown(path,deploy.pw_uid,deploy.pw_gid)
Path('/tmp/artifact.txt').write_bytes(b'fixture revision one\n')
Path('/tmp/tree/entry.txt').write_bytes(b'fixture tree entry\n')
for path in ('/tmp/artifact.txt','/tmp/tree/entry.txt'):os.chmod(path,0o644)
Path('/tmp/sshd-config').write_text(''' + '"""' + r'''
Port 22
ListenAddress 127.0.0.1
HostKey /tmp/host-key
PidFile /tmp/sshd.pid
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
PermitRootLogin no
PubkeyAuthentication yes
UsePAM no
StrictModes yes
AllowUsers deploy observer
AuthorizedKeysFile .ssh/authorized_keys
Subsystem sftp internal-sftp
LogLevel ERROR
''' + '"""' + r''')
subprocess.run(['/usr/sbin/sshd','-f','/tmp/sshd-config'],check=True)
'''


class FixtureUnavailable(RuntimeError):
    pass


class SSHFixture:
    def __init__(self, engine=None, image=DEFAULT_IMAGE):
        self.engine = engine or shutil.which("podman") or shutil.which("docker")
        if not self.engine:
            raise FixtureUnavailable("container engine unavailable")
        self.image = image
        self.name = "harness-ssh-" + uuid.uuid4().hex
        self.started = False
        self.mutations = 0
        self.lock = json.loads((HERE / "fixture-lock.json").read_text(encoding="utf-8"))
        self.versions = {}

    def _engine(self, *args, input=None, check=True, timeout=40):
        result = subprocess.run([self.engine, *args], input=input, capture_output=True,
                                timeout=timeout)
        if check and result.returncode:
            # Never echo input, key files or full container logs.
            raise FixtureUnavailable("container operation failed (exit %d)" % result.returncode)
        return result

    def build(self):
        pull = "--pull=false" if Path(self.engine).name == "docker" else "--pull=never"
        self._engine("build", pull, "-t", self.image,
                     "-f", str(HERE / "Dockerfile"), str(HERE), timeout=900)

    def __enter__(self):
        inspected = self._engine("image", "inspect", self.image, check=False)
        if inspected.returncode:
            raise FixtureUnavailable("pinned fixture image is not built")
        try:
            # A timed-out create can still have created a container. Cleanup by
            # this fresh, exact name even when run does not return successfully.
            self.started = True
            self._engine("run", "--detach", "--rm", "--network=none", "--name", self.name,
                         "--label", "harness.fixture=ssh-050", "--entrypoint", "/bin/sleep",
                         self.image, "600")
            self._engine("exec", "-i", self.name, "python3", "-",
                         input=INITIALIZE.encode("utf-8"))
            package_list = self._engine("exec", self.name, "dpkg-query", "-W",
                                        "-f=${Package}=${Version}\n").stdout.decode("utf-8")
            actual = dict(line.split("=", 1) for line in package_list.splitlines())
            if any(actual.get(name) != version for name, version in self.lock["packages"].items()):
                raise FixtureUnavailable("fixture package pin mismatch")
            self.versions = {
                "ssh": self._engine("exec", self.name, "ssh", "-V").stderr.decode().strip(),
                "rsync": self._engine("exec", self.name, "rsync", "--version").stdout.decode().splitlines()[0],
                "image_id": json.loads(inspected.stdout)[0]["Id"],
            }
            # Probe readiness with the observer's actual constrained credential.
            deadline = time.monotonic() + 5
            while True:
                result = self.execute(self.ssh("observer", "test -d /srv/site/releases"))
                if result.returncode == 0:
                    break
                if time.monotonic() >= deadline:
                    raise FixtureUnavailable("observer SSH readiness failed")
                time.sleep(.05)
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.started:
            result = self._engine("rm", "--force", self.name, check=False)
            if result.returncode:
                # An engine error does not establish that the container is gone.
                # Keep this exact name owned so a later close can retry cleanup.
                raise FixtureUnavailable("fixture cleanup failed")
            self.started = False

    def __exit__(self, *_):
        self.close()

    @staticmethod
    def ssh(user, remote):
        key = "/root/.ssh/id_ed25519" if user == "deploy" else "/tmp/observer-key"
        return ("ssh -p 22 -i " + key + " -o BatchMode=yes -o IdentitiesOnly=yes "
                "-o StrictHostKeyChecking=yes -o UserKnownHostsFile=/root/.ssh/known_hosts "
                + user + "@127.0.0.1 " + shlex.quote(remote))

    @staticmethod
    def upload():
        return ("scp -P 22 -i /root/.ssh/id_ed25519 -o BatchMode=yes -o IdentitiesOnly=yes "
                "-o StrictHostKeyChecking=yes -o UserKnownHostsFile=/root/.ssh/known_hosts "
                "/tmp/artifact.txt deploy@127.0.0.1:/srv/site/releases/artifact.txt")

    @staticmethod
    def policy():
        return {"schema_version": 1, "destinations": {
            "worker": {"host": "127.0.0.1", "user": "deploy", "port": 22},
            "observer": {"host": "127.0.0.1", "user": "observer", "port": 22}}, "rules": [
                {"id": "upload", "decision": "allow", "transport": "scp", "destination": "worker",
                 "remote_root": "/srv/site/releases"},
                {"id": "sync", "decision": "allow", "transport": "rsync", "destination": "worker",
                 "remote_root": "/srv/site/releases"},
                {"id": "promote", "decision": "allow", "transport": "ssh", "destination": "worker",
                 "argv": ["mv", "/srv/site/releases/artifact.txt", TARGET]},
                {"id": "read", "decision": "allow", "transport": "ssh", "destination": "observer",
                 "argv": ["cat", TARGET]},
                {"id": "protect", "decision": "deny", "transport": "ssh", "destination": "worker",
                 "operation": "remove", "remote_root": TARGET},
            ]}

    def execute(self, command, *, mutation=False):
        # shlex only decodes local fixture argv. OpenSSH itself performs the
        # remote argument joining and shell execution exercised by these tests.
        argv = shlex.split(command)
        if not argv or argv[0] not in {"ssh", "scp", "rsync"}:
            raise ValueError("fixture accepts only explicit transport invocations")
        if mutation:
            self.mutations += 1
        return self._engine("exec", self.name, *argv, check=False)

    def smoke(self):
        uploaded = self.execute(self.upload(), mutation=True)
        renamed = self.execute(self.ssh("deploy", "mv /srv/site/releases/artifact.txt " + TARGET), mutation=True)
        readback = self.execute(self.ssh("observer", "cat " + TARGET))
        denied = self.execute(self.ssh("observer", "mkdir /srv/site/releases/observer-write"))
        absent = self.execute(self.ssh("observer", "test -e /srv/site/releases/observer-write"))
        unchanged = self.execute(self.ssh("observer", "cat " + TARGET))
        synced = self.execute("rsync -a /tmp/tree/ deploy@127.0.0.1:/srv/site/releases/tree/", mutation=True)
        tree = self.execute(self.ssh("observer", "cat /srv/site/releases/tree/entry.txt"))
        passed = (uploaded.returncode == renamed.returncode == readback.returncode == 0
                  and readback.stdout == PAYLOAD and denied.returncode != 0
                  and absent.returncode == 1 and unchanged.stdout == PAYLOAD
                  and synced.returncode == tree.returncode == 0 and tree.stdout == b"fixture tree entry\n")
        return {"schema_version": 1, "passed": passed, "versions": self.versions,
                "expected_sha256": hashlib.sha256(PAYLOAD).hexdigest(),
                "observed_sha256": hashlib.sha256(readback.stdout).hexdigest(),
                "observer_write_exit": denied.returncode, "mutation_count": self.mutations,
                "network": "container loopback only", "guard_qualified": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine")
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--build", action="store_true", help="explicitly build pinned fixture image")
    args = parser.parse_args(argv)
    try:
        fixture = SSHFixture(args.engine, args.image)
        if args.build:
            fixture.build()
        with fixture:
            result = fixture.smoke()
        print(json.dumps(result, sort_keys=True))
        return 0 if result["passed"] else 1
    except (OSError, subprocess.SubprocessError, FixtureUnavailable):
        print("SSH fixture unavailable or failed; no qualification claimed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
