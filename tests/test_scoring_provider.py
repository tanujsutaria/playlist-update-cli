from scoring import _MAX_RUN_DEPTH, WebSearchScoreProvider


def _make_provider():
    return WebSearchScoreProvider(commands={"web": "x"}, timeout_sec=10)


def test_run_command_nonexistent_executable_fails_gracefully(monkeypatch):
    # A command whose executable does not resolve must fail gracefully (return
    # an empty score map, not raise) and must never spawn a subprocess.
    def fail_run(*args, **kwargs):
        raise AssertionError("subprocess.run should not be called for a missing executable")

    monkeypatch.setattr("scoring.subprocess.run", fail_run)

    provider = _make_provider()
    scores = provider._run_command("web", "definitely-not-a-real-binary-xyz --json", {"q": "x"})

    assert scores == {}


def test_run_command_retry_depth_is_bounded(monkeypatch):
    # Force the codex "unexpected argument" retry on every call and confirm the
    # recursion stops at the depth cap. The reported flag is not present in
    # argv, so _strip_flag is a no-op and the same argv recurses each time.
    calls = {"count": 0}

    class DummyResult:
        returncode = 1
        stdout = ""
        stderr = "error: unexpected argument '--bogus' found"

    def fake_run(*args, **kwargs):
        calls["count"] += 1
        return DummyResult()

    monkeypatch.setattr("scoring.subprocess.run", fake_run)
    # Make the executable resolve so we reach subprocess.run on each attempt.
    monkeypatch.setattr("scoring.shutil.which", lambda name: "/usr/bin/codex")

    provider = _make_provider()
    scores = provider._run_command("codex", "codex exec -", {"q": "x"})

    assert scores == {}
    # Initial call + at most _MAX_RUN_DEPTH retries before the guard trips.
    assert calls["count"] <= _MAX_RUN_DEPTH + 1
