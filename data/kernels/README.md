Place SPICE kernels for SMART in this directory.

Recommended seed set:

- `naif0012.tls` for leap seconds
- `pck00011.tpc` for planetary constants
- `de440.bsp` or mission-specific SPKs for ephemerides

The application does not auto-load kernels yet. Use the shared
`smart.services.spice_service.SpiceKernelManager` in future mission-analysis
modules to load the exact kernel set needed for the scenario.
