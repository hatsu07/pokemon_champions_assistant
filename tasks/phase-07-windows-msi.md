# Phase 7 — Windows EXE and MSI

Read `docs/Pokemon_Champions_WSL_Docker_Windows_MSI_Design_v2_6.md`. Do not begin packaging until a functional Windows desktop application exists.

- Separate Linux-testable logic from Windows capture and Credential Manager/DPAPI adapters.
- Write PyInstaller onedir spec, include PySide6, OCR language resources, fixed-master provenance; execute on GitHub Actions Windows runner.
- Write WiX per-user MSI source with persistent product identity, upgrade policy, shortcuts, and app files.
- Keep SQLite/settings/logs outside application install directory and retained on uninstall. Keys do not belong in MSI/config/logs.
- Test in clean Windows 10/11 x64 VM: install, launch, upgrade, uninstall, data retention, non-admin install if allowed.
- Create manifest/SHA-256 and release notes; code sign if configured; do not auto-release until manual approval and licensing review.
- Record actual Windows test results in `IMPLEMENTATION_STATUS.md`. Do not mark Phase 7 done based on Linux tests alone.
