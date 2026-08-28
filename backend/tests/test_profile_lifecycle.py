from pathlib import Path

import pytest

from app.services import profile_lifecycle


def test_cleanup_removes_runtime_artifacts_but_keeps_login_state(monkeypatch, tmp_path):
    profiles = tmp_path / "profiles"
    profile = profiles / "worker_reg_1"
    (profile / "cache2").mkdir(parents=True)
    (profile / "startupCache").mkdir()
    (profile / "cookies.sqlite").write_text("cookie")
    (profile / "storage" / "default" / "https+++auth.openai.com").mkdir(parents=True)
    (profile / "storage" / "default" / "https+++auth.openai.com" / "data.sqlite").write_text("auth")
    monkeypatch.setattr(profile_lifecycle.settings, "profiles_dir", str(profiles))

    result = profile_lifecycle.cleanup_profile_runtime_artifacts(profile)

    assert result == {"removed": 2, "failed": 0}
    assert not (profile / "cache2").exists()
    assert (profile / "cookies.sqlite").exists()
    assert (profile / "storage" / "default" / "https+++auth.openai.com" / "data.sqlite").exists()


def test_compact_profile_keeps_openai_storage_and_removes_other_storage(monkeypatch, tmp_path):
    profiles = tmp_path / "profiles"
    profile = profiles / "worker_reg_1"
    auth = profile / "storage" / "default" / "https+++auth.openai.com"
    other = profile / "storage" / "default" / "moz-extension+++example"
    auth.mkdir(parents=True)
    other.mkdir(parents=True)
    (auth / "auth.sqlite").write_text("auth")
    (other / "data.sqlite").write_text("extension")
    (profile / "places.sqlite").write_text("history")
    monkeypatch.setattr(profile_lifecycle.settings, "profiles_dir", str(profiles))

    profile_lifecycle.compact_profile(profile)

    assert (auth / "auth.sqlite").exists()
    assert not other.exists()
    assert not (profile / "places.sqlite").exists()


def test_profile_lifecycle_rejects_path_outside_profiles_root(monkeypatch, tmp_path):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(profile_lifecycle.settings, "profiles_dir", str(profiles))

    with pytest.raises(ValueError, match="profiles"):
        profile_lifecycle.remove_profile_tree(outside)


def test_profile_has_runtime_lock(monkeypatch, tmp_path):
    profiles = tmp_path / "profiles"
    profile = profiles / "worker_reg_1"
    profile.mkdir(parents=True)
    monkeypatch.setattr(profile_lifecycle.settings, "profiles_dir", str(profiles))

    assert profile_lifecycle.profile_has_runtime_lock(profile) is False
    (profile / "parent.lock").write_text("")
    assert profile_lifecycle.profile_has_runtime_lock(profile) is True
