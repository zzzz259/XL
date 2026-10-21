# Runtime provenance

The runtime core and fixed-endpoint HTTP client were supplied by the operator in
`starfall-refresh-kit-20261021.zip`, version 0.1.0, for integration in this project.
The source archive SHA256 is
`72adc2e1f8e181864394ee7e840bce12ef14d8acadbdd33dbeb2cbf7c9403816`.

Only public runtime sources are included. Account state, signatures, device values,
preparation tools, SDK files and private archives are excluded. The upstream core
and HTTP offline tests are retained under unique names in the Bot tests directory.

Integration changes: optional event tracking (disabled for the Bot adapter),
explicit direct transport, explicit proxy routing unaffected by NO_PROXY, support
for allowlisted gateway dictionary entries, ValueError-compatible safe errors,
and source formatting. Instance jobs retain ownership of monitoring and delivery.
