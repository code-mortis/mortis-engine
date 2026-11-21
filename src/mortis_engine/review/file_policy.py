"""Server-owned review and graph exclusions. Security scanners use their own policy."""

import re
from fnmatch import fnmatchcase
from pathlib import PurePosixPath

VERSION = 2
EXCLUDED_DIRS = {
    ".git", ".agents", ".claude", ".codex", ".cursor", ".aws", ".ssh", ".azure", ".kube",
    "node_modules", "vendor", "dist", "build", "target", ".venv", "venv", "__pycache__", "coverage", "generated",
    ".next", ".nuxt", ".cache", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".turbo",
}
EXTENSIONS = (
    "png jpeg jpg gif webp avif bmp ico tiff tif heic heif svg "
    "mp3 wav ogg flac aac m4a mp4 webm mov avi mkv mpeg mpg "
    "woff woff2 ttf otf eot zip tar gz tgz bz2 xz 7z rar pdf doc docx xls xlsx ppt pptx "
    "wasm node so dylib dll exe o obj a lib class jar pyc pyo pem key p12 pfx lock"
).split()
KEY_SUFFIXES = {"pem", "key", "p12", "pfx"}
NAMES = {
    "agents.md", "claude.md", "gemini.md", ".mcp.json", "copilot-instructions.md",
    "package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "bun.lockb", "yarn.lock",
    "pipfile.lock", "poetry.lock", "uv.lock", "cargo.lock", "composer.lock", "gemfile.lock",
    "go.sum", "packages.lock.json", "packages-lock.json",
}
MANDATORY = sorted({
    *(f"**/*.{ext}" for ext in EXTENSIONS), *(f"**/{name}" for name in NAMES),
    *(f"**/{name}/**" for name in EXCLUDED_DIRS),
    "**/.*review/**", "**/*.map", "**/*.min.js", "**/*.min.css", "**/.env*", "**/.env*/**",
    *(pattern for word in ("secret", "credential", "password", "privatekey", "private-key", "private_key")
      for pattern in (f"**/*{word}*", f"**/*{word}*/**")),
})
# Review text scope is independent of the structural parser's capabilities.
TEXT_CONTEXT = [f"**/*.{ext}" for ext in "md markdown mdx rst txt adoc json yaml yml toml sql html css astro".split()]


def patterns(value=""):
    if not isinstance(value, str) or len(value) > 4000:
        raise ValueError("Additional file exclusions must be at most 4,000 characters.")
    result = sorted({p.strip() for p in re.split(r"[,\n]", value) if p.strip()})
    if any(p.startswith("!") for p in result):
        raise ValueError("Additional file exclusions only remove files; inclusion overrides are not allowed.")
    return result


def policy(settings):
    return {"version": VERSION, "exclusions": patterns(settings.get("exclusions", ""))}


def matches(path, pattern):
    return (fnmatchcase(path, pattern) or (pattern.startswith("**/") and fnmatchcase(path, pattern[3:]))
            or path.startswith(pattern.rstrip("/") + "/"))


def template_source_path(path):
    while path.lower().endswith(".in"):
        path = path[:-3]
    return path


def excluded(path, rules=None):
    parts = PurePosixPath(path).parts
    if (not parts or path.startswith("/") or PurePosixPath(path).as_posix() != path or "\\" in path
        or ".." in parts or any(ord(c) < 32 or ord(c) == 127 for c in path)):
        return True
    return (not KEY_SUFFIXES.isdisjoint(parts[-1].lower().split(".")[1:])
            or any(matches(value.lower(), pattern) for value in (path, template_source_path(path))
                for pattern in MANDATORY)
            or any(matches(path, pattern) for pattern in (rules or {}).get("exclusions", [])))
