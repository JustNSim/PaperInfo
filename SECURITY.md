# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.1.x | Yes |
| Earlier development snapshots | No |

## Reporting a vulnerability

Please do not disclose vulnerabilities in a public Issue.

Use GitHub's **Report a vulnerability** option in the repository Security tab to submit a private vulnerability report. Include:

- the affected version or commit;
- reproduction steps;
- expected and actual behavior;
- potential impact;
- a suggested mitigation, if available.

The maintainer will acknowledge a valid report as soon as practical and coordinate disclosure after a fix is available.

## Deployment boundary

PaperInfo is designed as a single-user, local-first application. It defaults to `127.0.0.1` and does not include a multi-user authentication system.

Do not expose the development server directly to the public internet. For trusted LAN access, configure a strong `SECRET_KEY`, restrict access with a firewall or reverse proxy, keep Debug disabled, and treat all mutation endpoints as privileged.

Never commit `.env`, API keys, Webhook URLs, databases, logs, downloaded papers or cached source data.
