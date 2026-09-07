"""Desktop client release info: ``GET /desktop/version`` (PLAN.md §3.3).

The installed client asks the backend whether it is current (silently at start
and every 30 minutes) and gets back the latest version, whether the update is
required, and where the package lives. The source of truth is the electron-
builder feed (``latest.yml`` / ``latest-mac.yml``) that ``infra/publish-desktop.sh``
puts next to the installers — the same files electron-updater reads — so the
backend never has to be redeployed to announce a release.
"""
