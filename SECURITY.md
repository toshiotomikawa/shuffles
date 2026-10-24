# Security and data safety

Shuffles renames files in a trusted local Windows folder. Keep an independent backup before using it. It is not a sandbox for hostile processes, untrusted recovery files or shared/network/cloud folders. File identity checks reduce accidental substitution but do not provide transaction isolation against another process or guarantee durability after hardware/power failure.

There is no telemetry or network service. Logs and recovery journals contain local paths and filenames; keep them private. Do not post media, journals, machine paths, tokens or other personal data in public reports. Report a reproducible issue using synthetic filenames and dummy file contents. For sensitive reports, use the repository owner's private contact channel rather than a public issue.

The synthetic suite verifies core rename behavior only. It does not certify Explorer UI, installation, every filesystem, cloud provider or antivirus interaction. Preserve any interrupted-run journal and all temporary media files until recovery or backup restoration is complete.
