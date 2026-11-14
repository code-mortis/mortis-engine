import pytest

from .git_helpers import git


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-qb", "main")
    return tmp_path
