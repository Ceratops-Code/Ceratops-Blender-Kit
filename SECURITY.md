# Security Policy

## Reporting A Vulnerability

Use GitHub private vulnerability reporting for this repository. Do not open a
public issue for vulnerabilities, leaked credentials, or bypass techniques.

When reporting, include the affected component or file path, impact,
reproduction steps, and a suggested fix when known. The maintainer will triage
reports through GitHub security advisories.

## Scope

In scope:

- unsafe MCP tool behavior or input validation
- unintended filesystem access or version-boundary bypasses
- unsafe Blender subprocess handling
- credential, private endpoint, or local-path exposure

Out of scope:

- feature requests and artistic workflow preferences
- vulnerabilities in Blender or other third-party software that this project
  invokes but does not ship
