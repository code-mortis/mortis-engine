"""Run the bounded parser in a disposable Docker container without host mounts."""

import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from mortis_engine.review.errors import ReviewStopped

from .contracts import EXTRACTION_SECONDS, MAX_REQUEST_BYTES, MAX_RESULT_BYTES, CodeGraphResult
from .validation import validate_result

DEFAULT_IMAGE = "mortis-engine-parser:0.1.0"
APP_IMAGE = "code-mortis-code-graph:0.1.0"


def image_name(value):
    if not isinstance(value, str) or len(value) > 255 or not re.fullmatch(r"[a-z0-9][a-z0-9._/:@-]*", value):
        raise ValueError("Choose a valid locally built parser image name.")
    return value


def docker_environment(config):
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
    env["PATH"] = os.pathsep.join(p for p in env.get("PATH", os.defpath).split(os.pathsep) if Path(p).is_absolute())
    host = os.environ.get("DOCKER_HOST")
    if host:
        if not host.startswith("unix:///") or any(ord(char) < 32 for char in host):
            raise ValueError("Graph extraction requires a local Docker Unix socket.")
        env["DOCKER_HOST"] = host
    env["DOCKER_CONFIG"] = str(config)
    return env


def extract(request, *, image=DEFAULT_IMAGE, stopped=lambda: False):
    image = image_name(image)
    body = request.model_dump_json().encode()
    if len(body) > MAX_REQUEST_BYTES:
        raise ValueError("The code graph request exceeds its transport limit.")
    docker = shutil.which("docker", path=os.pathsep.join(
        p for p in os.environ.get("PATH", os.defpath).split(os.pathsep) if Path(p).is_absolute()))
    if docker is None:
        raise ValueError("Docker is required for the isolated code graph parser.")
    app_image = image == APP_IMAGE
    package = "code_mortis.schemas.code_graph" if app_image else "mortis_engine.code_graph.contracts"
    module = "code_mortis.code_graph.extract" if app_image else "mortis_engine.code_graph.extract"
    python = "/app/backend/.venv/bin/python" if app_image else "/usr/local/bin/python"
    program = (f"import sys; from {package} import CodeGraphRequest; from {module} import extract; "
               f"raw=sys.stdin.buffer.read({MAX_REQUEST_BYTES + 1}); "
               "request=CodeGraphRequest.model_validate_json(raw); "
               "sys.stdout.write(extract(request).model_dump_json())")
    name = "mortis-parser-" + uuid.uuid4().hex
    command = [docker, "run", "--rm", "--interactive", "--pull=never", "--name", name,
               "--network=none", "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m",
               "--memory=512m", "--memory-swap=512m", "--cpus=1", "--pids-limit=64",
               "--cap-drop=ALL", "--security-opt=no-new-privileges", "--user=65534:65534",
               "--ulimit", "fsize=33554432:33554432", "--entrypoint", python, image, "-c", program]
    with tempfile.TemporaryDirectory(prefix="mortis-parser-client-") as directory:
        env = docker_environment(Path(directory))
        with tempfile.TemporaryFile() as supplied, tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            supplied.write(body)
            supplied.seek(0)
            if stopped():
                raise ReviewStopped("Code graph extraction cancelled.")
            process = subprocess.Popen(command, env=env, stdin=supplied, stdout=output, stderr=errors,
                                       start_new_session=True)
            deadline = time.monotonic() + EXTRACTION_SECONDS
            try:
                while process.poll() is None:
                    if stopped():
                        raise ReviewStopped("Code graph extraction cancelled.")
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Code graph extraction reached its 120 second limit.")
                    if os.fstat(output.fileno()).st_size > MAX_RESULT_BYTES or os.fstat(errors.fileno()).st_size > 2_000_000:
                        raise ValueError("The code graph parser exceeded its output limit.")
                    try:
                        process.wait(timeout=0.1)
                    except subprocess.TimeoutExpired:
                        pass
                if process.returncode:
                    raise ValueError("The local parser image could not extract this snapshot. Build a compatible image and check Docker.")
                output.seek(0)
                raw = output.read(MAX_RESULT_BYTES + 1)
                if len(raw) > MAX_RESULT_BYTES:
                    raise ValueError("The code graph parser exceeded its output limit.")
                result = CodeGraphResult.model_validate_json(raw)
                if not {"schema_version", "extractor_version"} <= result.model_fields_set:
                    raise ValueError("The parser omitted its explicit protocol identity.")
                return validate_result(request, result)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                # Removing by our generated name also stops daemon-side work after client cancellation.
                subprocess.run([docker, "rm", "--force", name], env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
