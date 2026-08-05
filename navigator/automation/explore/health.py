"""Operator health check: validate active flows before a prospect sees them.

Not a daemon. Operators wire cron / systemd:

    python -m navigator.automation.explore.health --product-id X

Decrypts the Client's product password via CredentialVault to drive a real
login, replays each flow, and writes `_meta.validation` verdicts into an
unpublished draft revision. Publish stays human.

Security:
  - never runs against origin public_embed traffic (this is loopback/operator)
  - never logs the plaintext password
  - requires NAVIGATOR_CREDENTIAL_KEY or exits without attempting anything
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from navigator.app.credential_vault import (
    CredentialVault,
    VaultNotConfigured,
)
from navigator.automation.explore import validate
from navigator.automation.explore.runner import _attach_meta
from navigator.core.settings import settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate drafted flows for one product (operator / cron)."
    )
    parser.add_argument("--product-id", required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Score from semantics only — no browser, no credential decrypt.",
    )
    args = parser.parse_args(argv)

    # Fail before touching anything if the vault key is missing (unless dry-run).
    if not args.dry_run:
        try:
            from navigator.app.credential_vault import _cipher

            _cipher()
        except VaultNotConfigured as exc:
            print(f"health: abort — {exc}", file=sys.stderr)
            return 2

    return run_health(args.product_id, dry_run=args.dry_run)


def run_health(product_id: str, *, dry_run: bool = False) -> int:
    """Validate flows; write verdicts. 0 = all ready/review, 1 = any broken, 2 = config."""
    from navigator.app.main import get_registry
    from navigator.knowledge.site_graph import parse_site_graph

    registry = get_registry()
    try:
        rev = registry.latest_revision(product_id)
    except Exception as exc:  # noqa: BLE001
        print(f"health: no site graph for {product_id!r}: {exc}", file=sys.stderr)
        return 2

    graph = parse_site_graph(rev.yaml)
    yaml_text = rev.yaml
    any_broken = False

    if dry_run:
        for page_id, page in graph.pages.items():
            for flow_id, steps in page.flows.items():
                sem = graph.flow_semantics(flow_id)
                descs = [
                    str(s.get("description") or "")
                    for s in (sem.get("steps") or [])
                    if isinstance(s, dict)
                ]
                result = validate.verdict_for(
                    purpose=str(sem.get("purpose") or ""),
                    tags=sem.get("tags") if isinstance(sem.get("tags"), list) else (),
                    step_descriptions=descs,
                    n_steps=len(steps),
                    pass_rate=1.0,  # dry-run assumes replay would pass
                )
                yaml_text = _attach_meta(
                    yaml_text, "validation", flow_id, result.as_dict()
                )
                print(
                    f"health: {flow_id} → {result.verdict} "
                    f"(risk={result.risk_score:.0f}, dry-run)"
                )
                if result.verdict == "broken":
                    any_broken = True
        registry.put_site_graph(product_id, yaml_text, "explored", publish=False)
        return 1 if any_broken else 0

    # Live path: login + replay. Password never printed.
    creds = None
    try:
        with CredentialVault(settings.credential_db_path) as vault:
            creds = vault.credentials_for(product_id)
    except VaultNotConfigured as exc:
        print(f"health: abort — {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"health: vault error: {type(exc).__name__}", file=sys.stderr)
        return 2

    if creds is None:
        print(
            f"health: no credentials stored for {product_id!r} — cannot replay",
            file=sys.stderr,
        )
        return 2

    login_url, username, _password = creds
    # Deliberately bind password only inside the browser context below; never
    # interpolate it into log lines.
    print(
        f"health: logging in as {username!r} at {login_url!r} "
        f"(password redacted)"
    )

    try:
        any_broken = _replay_all(
            product_id=product_id,
            graph=graph,
            yaml_text_holder={"yaml": yaml_text},
            login_url=login_url,
            username=username,
            password=creds[2],
            registry=registry,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"health: replay failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 1 if any_broken else 0


def _replay_all(
    *,
    product_id: str,
    graph: Any,
    yaml_text_holder: dict[str, str],
    login_url: str,
    username: str,
    password: str,
    registry: Any,
) -> bool:
    from playwright.sync_api import sync_playwright

    from navigator.automation.browser import tools as browser_tools
    from navigator.automation.browser import verify as browser_verify
    from navigator.automation.browser.login_gate import LoginGateResult, run_login_gate
    from navigator.automation.browser.product_login import login_product

    any_broken = False
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        try:
            result = run_login_gate(
                login_fn=lambda **kw: login_product(page, **kw),
                url=login_url,
                email=username,
                password=password,
            )
            if result is LoginGateResult.failed:
                print("health: product login failed", file=sys.stderr)
                return True

            for page_id, page_spec in graph.pages.items():
                for flow_id, steps in page_spec.flows.items():
                    sem = graph.flow_semantics(flow_id)
                    descs = [
                        str(s.get("description") or "")
                        for s in (sem.get("steps") or [])
                        if isinstance(s, dict)
                    ]
                    outcome = validate.validate_flow(
                        steps=steps,
                        page=page,
                        graph=graph,
                        page_id=page_id,
                        execute=browser_tools.execute,
                        verify=browser_verify.check,
                        purpose=str(sem.get("purpose") or ""),
                        tags=(
                            sem.get("tags")
                            if isinstance(sem.get("tags"), list)
                            else ()
                        ),
                        step_descriptions=descs,
                    )
                    yaml_text_holder["yaml"] = _attach_meta(
                        yaml_text_holder["yaml"],
                        "validation",
                        flow_id,
                        outcome.as_dict(),
                    )
                    print(
                        f"health: {flow_id} → {outcome.verdict} "
                        f"(pass={outcome.pass_rate:.2f}, risk={outcome.risk_score:.0f})"
                    )
                    if outcome.verdict == "broken":
                        any_broken = True
        finally:
            context.close()
            browser.close()

    registry.put_site_graph(
        product_id, yaml_text_holder["yaml"], "explored", publish=False
    )
    return any_broken


if __name__ == "__main__":
    raise SystemExit(main())
