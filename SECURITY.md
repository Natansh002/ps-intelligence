# Security policy

## Reporting
Please report suspected vulnerabilities privately via GitHub Security Advisories
(the **Security** tab → *Report a vulnerability*), not a public issue.

## Scope and design
- The published site (`docs/index.html`) is a **single self-contained static
  file**: no server, no backend, and **no network calls** except web fonts. It
  takes no user input and stores nothing, so it has no server-side, injection,
  or data-exfiltration surface.
- The build pipeline is **standard-library Python** (no third-party runtime
  dependencies), which keeps the supply-chain surface minimal.
- CI runs with **least-privilege** `GITHUB_TOKEN` permissions
  (`contents: read`), and third-party GitHub Actions are **pinned to commit
  SHAs**.
- The demonstration data is **generated (synthetic)** — no real company,
  customer, or financial data.

## Enabled protections
Secret scanning + push protection, Dependabot alerts, and force-push/deletion
protection on `main`.
