"""Tests for config loading, saving, and repo alias resolution."""

import os
import json
import tempfile
import shutil

from pixelferry.config import load_config, save_config, resolve_repo, _find_config


def test_load_config_missing():
    """Loading config when no file exists returns empty repos."""
    # _find_config searches from CWD upward, so in a temp dir with no config,
    # it should return None and load_config returns {"repos": {}}
    tmpdir = tempfile.mkdtemp()
    try:
        old_cwd = os.getcwd()
        os.chdir(tmpdir)
        try:
            cfg = load_config()
            assert cfg == {"repos": {}}
        finally:
            os.chdir(old_cwd)
    finally:
        shutil.rmtree(tmpdir)


def test_save_and_load_roundtrip():
    """Save config to a file and load it back."""
    tmpdir = tempfile.mkdtemp()
    try:
        cfg_path = os.path.join(tmpdir, "pixelferry.json")
        cfg = {"repos": {"myrepo": "/some/path", "other": "/other/path"}}

        save_config(cfg, cfg_path)
        assert os.path.exists(cfg_path)

        loaded = load_config(cfg_path)
        assert loaded["repos"]["myrepo"] == "/some/path"
        assert loaded["repos"]["other"] == "/other/path"
    finally:
        shutil.rmtree(tmpdir)


def test_load_config_without_repos_key():
    """Config file missing 'repos' key gets it added automatically."""
    tmpdir = tempfile.mkdtemp()
    try:
        cfg_path = os.path.join(tmpdir, "pixelferry.json")
        with open(cfg_path, "w") as f:
            json.dump({"other_key": "value"}, f)

        loaded = load_config(cfg_path)
        assert "repos" in loaded
        assert loaded["repos"] == {}
    finally:
        shutil.rmtree(tmpdir)


def test_resolve_repo_direct_path():
    """resolve_repo with a valid directory returns its absolute path."""
    tmpdir = tempfile.mkdtemp()
    try:
        result = resolve_repo(tmpdir)
        assert os.path.isdir(result)
        assert os.path.isabs(result)
    finally:
        shutil.rmtree(tmpdir)


def test_resolve_repo_alias():
    """resolve_repo with a config alias returns the mapped path."""
    tmpdir = tempfile.mkdtemp()
    try:
        repo_dir = os.path.join(tmpdir, "my_project")
        os.makedirs(repo_dir)

        cfg_path = os.path.join(tmpdir, "pixelferry.json")
        save_config({"repos": {"proj": repo_dir}}, cfg_path)

        old_cwd = os.getcwd()
        os.chdir(tmpdir)
        try:
            result = resolve_repo("proj")
            assert result == os.path.abspath(repo_dir)
        finally:
            os.chdir(old_cwd)
    finally:
        shutil.rmtree(tmpdir)


def test_resolve_repo_invalid():
    """resolve_repo with invalid spec raises ValueError."""
    try:
        resolve_repo("nonexistent_alias_xyz")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_resolve_repo_alias_not_exist():
    """resolve_repo with alias pointing to nonexistent dir raises ValueError."""
    tmpdir = tempfile.mkdtemp()
    try:
        cfg_path = os.path.join(tmpdir, "pixelferry.json")
        save_config({"repos": {"dead": "/nonexistent/path/xyz"}}, cfg_path)

        old_cwd = os.getcwd()
        os.chdir(tmpdir)
        try:
            try:
                resolve_repo("dead")
                assert False, "Should have raised ValueError"
            except ValueError as e:
                assert "does not exist" in str(e)
        finally:
            os.chdir(old_cwd)
    finally:
        shutil.rmtree(tmpdir)


def test_save_config_writes_to_discovered_location():
    """save_config without path writes to the discovered config or home dir."""
    tmpdir = tempfile.mkdtemp()
    try:
        # Create an existing config in tmpdir
        cfg_path = os.path.join(tmpdir, "pixelferry.json")
        save_config({"repos": {"a": "/a"}}, cfg_path)

        old_cwd = os.getcwd()
        os.chdir(tmpdir)
        try:
            # save_config should find and overwrite the existing config
            save_config({"repos": {"b": "/b"}})
            loaded = load_config(cfg_path)
            assert "b" in loaded["repos"]
        finally:
            os.chdir(old_cwd)
    finally:
        shutil.rmtree(tmpdir)
