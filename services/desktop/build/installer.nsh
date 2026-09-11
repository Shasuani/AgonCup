; LeagueOfLeagues — NSIS customisations picked up by electron-builder (nsis.include).
;
; Tolerate a failing PREVIOUS uninstaller when upgrading. The 0.2.0 installer
; shipped electron-builder 24.13.3, whose "is the app running?" check misfires
; on some PCs (tasklist | find with no anchoring — electron-userland/
; electron-builder #6865 / #8131). Its uninstaller then exits with code 2
; before touching a file, and the stock installer treats that as fatal
; ("Uninstall was not successful. Uninstaller error code: 2") — so the
; affected player could never install a newer version at all.
;
; The new installer has already closed a running app itself by the time this
; runs (CHECK_APP_RUNNING precedes uninstallOldVersion), and the fresh install
; rewrites every file, shortcut and registry value the old uninstaller would
; have removed. So: log it and carry on instead of quitting.
;
; No labels here: electron-builder inserts this macro more than once inside
; the same NSIS function (handleUninstallResult), and labels must be unique.
!macro customUnInstallCheck
  ${if} ${errors}
    ClearErrors
    DetailPrint "Previous version's uninstaller could not be launched - continuing."
  ${elseif} $R0 != 0
    DetailPrint "Previous version's uninstaller exited with code $R0 - continuing with the install."
  ${endif}
!macroend

!macro customUnInstallCheckCurrentUser
  !insertmacro customUnInstallCheck
!macroend
