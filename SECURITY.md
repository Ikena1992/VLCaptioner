# Security policy

## Reporting a vulnerability

Please avoid posting API keys, private datasets, or vulnerability details in a
public issue. Use GitHub's **Security** tab and **Report a vulnerability** when
private reporting is enabled for this repository. If that option is unavailable,
open an issue requesting a private contact channel without including exploit
details or sensitive information.

A useful private report describes the affected revision, reproduction steps,
impact, and a minimal example with sensitive data removed.

## Configuration and data

Keep `config.txt`, authentication tokens, and local caches outside Git. Review
logs and screenshots before sharing them. If a credential is exposed, revoke
it with its provider and replace it locally.

A remote Ollama server receives the images and prompts sent for inference.
Restrict its API to trusted computers; do not expose it directly to the internet.
Third-party models and downloaded datasets have their own access and license terms.
