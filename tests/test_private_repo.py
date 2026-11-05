from scanner import private_repo


def test_troostwijk_memory_is_saved_encrypted(tmp_path, monkeypatch):
    monkeypatch.delenv("INVENTORY_KEY", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    path = tmp_path / "troostwijk.enc"
    state = {"seen": {}, "troostwijk_alerts": {"lots": {"A1-1-2": {"url": "https://www.troostwijkauctions.com/nl/l/A1-1-2"}}}}
    private_repo.save_secret_state(state, path)
    assert "troostwijk_alerts" not in state and b"troostwijkauctions" not in path.read_bytes()
    back = {"seen": {}}
    private_repo.load_secret_state(back, path)
    assert back["troostwijk_alerts"]["lots"]["A1-1-2"]["url"].endswith("A1-1-2")


def test_without_a_key_the_memory_stays_in_the_state(tmp_path, monkeypatch):
    monkeypatch.delenv("INVENTORY_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    state = {"troostwijk_alerts": {"lots": {}}}
    private_repo.save_secret_state(state, tmp_path / "x.enc")
    assert "troostwijk_alerts" in state and not (tmp_path / "x.enc").exists()


def test_no_token_no_upload(monkeypatch):
    monkeypatch.delenv("PRIVATE_REPO_TOKEN", raising=False)
    assert private_repo.put_json("data/x.json", {}, "x") is False


def test_troostwijk_site_cache_is_encrypted_too(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    path = tmp_path / "t.enc"
    state = {"site_cache": {"hnvi": {"a": 1}, "troostwijk": {"auctions": {"A1-1": {"lots": []}}}}}
    private_repo.save_secret_state(state, path)
    assert state["site_cache"] == {"hnvi": {"a": 1}}
    private_repo.load_secret_state(state, path)
    assert state["site_cache"]["troostwijk"] == {"auctions": {"A1-1": {"lots": []}}}
