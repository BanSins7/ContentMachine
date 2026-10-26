# RTB — GitHub Actions queue / QA blocker (2026-10-26)

## Evidence from actual GitHub API

Repo: `BanSins7/ContentMachine`, PR [#32](https://github.com/BanSins7/ContentMachine/pull/32), branch `feature/rtb-module-v2`.

On commit `110ddd35cbcd53d910f2484fb8484f01c127d82f`:
- Windows D1 smoke: run **40299686256**, `queued`.
- ContentMachine checks: run **40299686274**, `queued`.
- RTB module contract regression: run **40299686277**, `queued`.
- Knowledge B Stage14 fast QA: run **40299686432**, `queued`.
- RTB Linux job **121494225049**, RTB PostgreSQL17 job **121494225311**, RTB Windows job **121494225369**: `queued`, `runner_name` absent.
- `fetch_workflow_job_steps(121494225049)` returned `steps: []`.

Subsequent GitHub commits showed the same behavior. On `138774253791d6dfab9f0fcd5690b98b48e8ee7f`, all four workflows, including preexisting B Stage14 and Windows D1 smoke, are also queued. **No test logs can exist until a job is picked up.** Do not call queued tests failed, passed or completed.

This is a GitHub Actions queue/scheduling limitation, not currently a demonstrated Python/SQL regression. Since even unrelated workflows are queued, creating another workflow or repeatedly pushing commits cannot reliably repair the runner-side issue.

## Remediation to verify as repository owner

1. Open [PR #32 checks](https://github.com/BanSins7/ContentMachine/pull/32/checks) or GitHub repository **Actions** and open the oldest queued workflow. Note whether GitHub displays a `Waiting for a runner`, Actions-disabled, billing/usage-limit or job approval message.
2. Repository **Settings → Actions → General**: verify actions and third-party actions are allowed; for forks/public contributions check whether first-time contributor jobs require manual approval. Do not loosen security settings indiscriminately.
3. Repository **Settings → Actions → Runners**: check runner availability, labels and whether GitHub-hosted Linux/Windows runners are enabled for this account and repository. This repository's RTB job targets `ubuntu-latest` and `windows-2025`.
4. Account or organization **Billing and licensing → Actions**: check available GitHub Actions minutes and any spending/usage limit; verify no account or organization-wide restrictions. Only an owner can resolve restrictions.
5. Once unblocked, open the latest commit on PR #32, run all 4 workflows and inspect actual red step output before changing code. Re-run failed jobs only **after** runs complete with failure; queued jobs cannot be treated as failed.
6. If hosted Actions are unavailable, clone the branch on a developer machine/CI with PostgreSQL17 and run the same commands below. Report exact output and exit status, not a claimed success from an unexecuted suite.

## Standalone local commands (never invoke OpenRouter)

```bash
git switch feature/rtb-module-v2
python -m pip install -r intelligence/requirements.txt
python -m compileall -q intelligence/taxonomy_builder
python -m unittest discover -s tests -p 'test_taxonomy_c_corrector_contracts.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_research_input.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_lifecycle.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_quote.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_validation.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_provider.py' -v
# Only when a real PostgreSQL17 test database is configured via CM_TEST_PG_DSN:
python -m intelligence.migrate
python -m unittest discover -s tests -p 'test_taxonomy_research_input_db.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_binding_db.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_ledger_db.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_quote_db.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_pass1_db.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_db.py' -v
```

**Do not disable money safety gates or mark QA PASS to bypass a blocked CI.**
Mark `[x]` + ~~strikethrough~~ when implementation is complete, but QA remains explicitly `QUEUED / NOT RUN` until executed with `success`.
