from __future__ import annotations

from hc.diagnostics import KNOWN_ISSUES, detect_issues


# Реальный фрагмент лога из issue пользователя — самый важный кейс.
REAL_MASTER_KEY_LOG = """
======================================================================
✅ All startup checks passed!
======================================================================

[SecureStorage] Root hash verified: 1486f57dc1b28a71...
[Runtime] SecretStore not available: Decryption failed for secret 'runtime.csrf_secret': InvalidTag. Typical cause: vault was recreated or passphrase changed (RUNTIME_MASTER_KEY); re-add the credential secret.
Traceback (most recent call last):
  File "/app/main.py", line 146, in <module>
    asyncio.run(main())
  File "/app/app/env_bootstrap.py", line 250, in preflight_check
    raise RuntimeError(
RuntimeError: Missing required secrets:
  - CSRF_SECRET
  - OAUTH_ENCRYPTION_KEY
"""


def test_known_issues_have_unique_ids() -> None:
    ids = [i.id for i in KNOWN_ISSUES]
    assert len(ids) == len(set(ids)), f"duplicate KNOWN_ISSUES.id: {ids}"


def test_known_issues_have_non_empty_titles_and_causes() -> None:
    for issue in KNOWN_ISSUES:
        assert issue.title.strip(), f"{issue.id} has empty title"
        assert issue.cause.strip(), f"{issue.id} has empty cause"
        assert issue.pattern, f"{issue.id} has empty pattern"


def test_detect_master_key_mismatch_from_real_log() -> None:
    found = detect_issues(REAL_MASTER_KEY_LOG, service="core-runtime")
    ids = {d.issue.id for d in found}
    # Реальный лог триггерит сразу обе известные болячки: и сам InvalidTag,
    # и следующее за ним Missing required secrets.
    assert "master_key_mismatch" in ids
    assert "missing_required_secrets" in ids


def test_detect_returns_each_issue_once_even_if_log_has_many_matches() -> None:
    text = "Decryption failed: InvalidTag\nDecryption failed: InvalidTag\nDecryption failed: InvalidTag"
    found = detect_issues(text)
    master_key_hits = [d for d in found if d.issue.id == "master_key_mismatch"]
    assert len(master_key_hits) == 1


def test_detect_empty_input() -> None:
    assert detect_issues("") == []
    assert detect_issues("ничего особенного, всё работает") == []


def test_detect_port_already_in_use() -> None:
    text = "Error response from daemon: Bind for 0.0.0.0:18080 failed: port is already allocated"
    found = detect_issues(text)
    assert any(d.issue.id == "port_already_in_use" for d in found)


def test_detect_postgres_connection_refused() -> None:
    text = """
psycopg2.OperationalError: could not connect to server: Connection refused
        Is the server running on host "postgres" (172.18.0.2) and accepting
        TCP/IP connections on port 5432?
""".strip()
    found = detect_issues(text)
    assert any(d.issue.id == "postgres_connection_refused" for d in found)


def test_detect_attaches_service_name() -> None:
    found = detect_issues("RUNTIME_MASTER_KEY is required", service="core-runtime")
    assert found
    assert found[0].service == "core-runtime"


def test_detect_docker_network_not_found() -> None:
    text = (
        "Error response from daemon: failed to set up container networking: "
        "network 838e2a1286e8efcac42309af0297505022a8ed53a31880cdf042254eb34f86b1 not found"
    )
    found = detect_issues(text)
    assert any(d.issue.id == "docker_network_not_found" for d in found)


def test_detect_frontend_workspace_missing() -> None:
    text = """
! Corepack is about to download https://registry.npmjs.org/pnpm/-/pnpm-11.1.2.tgz
[ERR_PNPM_NO_PKG_MANIFEST] No package.json found in /workspace
[ERR_PNPM_NO_PKG_MANIFEST] No package.json found in /workspace
""".strip()
    found = detect_issues(text, service="frontend-vite")
    matched = [d for d in found if d.issue.id == "frontend_workspace_missing"]
    assert matched
    assert matched[0].service == "frontend-vite"
