import pytest


@pytest.fixture(autouse=True)
def _quota_file(tmp_path, monkeypatch):
    """Keep the Gemini quota pause out of the repo's data/ dir and away from other tests."""
    monkeypatch.setattr("docex.providers.llm.QUOTA_FILE", tmp_path / "gemini_quota.json")
