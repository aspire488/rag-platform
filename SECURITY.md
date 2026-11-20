# Security policy

## Supported version

Security fixes target the latest code on the default branch. This repository is an evolving local-first project and does not currently publish a formal supported-release matrix.

## Reporting a vulnerability

Do not open a public issue containing exploit details, private documents, credentials, tokens, or personal data.

Use GitHub's **Report a vulnerability** workflow for this repository when available. Include:
- affected commit or version;
- impact and preconditions;
- a minimal reproduction that uses synthetic data;
- suggested mitigation, if known.

If private reporting is unavailable, contact the repository owner through GitHub privately and request a secure channel. Do not send secrets or user data.

## Security design constraints

The project intentionally defaults to local-only operation, rejects unsafe path escapes, redacts logs, bounds inputs, and keeps generation/egress disabled unless explicitly approved. Security reports should identify any bypass of those guarantees.
