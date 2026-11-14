import subprocess


def git(repo, *args):
    result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=Mortis Test", "-c", "user.email=test@localhost",
                             "-c", "commit.gpgsign=false", *args], cwd=repo, check=True,
                            capture_output=True, text=True)
    return result.stdout.strip()


def commit(repo, files):
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "fixture")
    return git(repo, "rev-parse", "HEAD")
