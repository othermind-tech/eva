; custom.nsh — EVA NSIS customisation
;
; 1. customHeader    → overrides branding text on every installer/uninstaller page
; 2. customUnInit    → shows nsDialogs checkbox before the wizard (default: ticked)
; 3. customUnInstall → deletes $APPDATA\EVA and EVA's Credential Manager entries
;                       (<NAME>@eva) only if box was ticked
;
; Variables are declared /GLOBAL inside customUnInit (uninstaller scope)
; so they don't appear unused in the installer and trigger NSIS warnings.
;
; Per-user vs machine-wide installs: with "Install for anyone using this computer"
; the installer runs in "all users" shell context, where $APPDATA is
; C:\ProgramData. Electron keeps user data in the per-user %APPDATA%\EVA, so every
; macro below that touches user data switches to the current user's context first
; (EVA_UserContext / EVA_RestoreContext), the same thing electron-builder does for
; its own app-data removal. For a per-user install both macros do nothing.

; Name of the user-data folder under %APPDATA% (Electron's userData = productName).
; A define so a test build can aim at a dummy folder (makensis /DEVA_DATA_SUBDIR=EVAtest).
!ifndef EVA_DATA_SUBDIR
  !define EVA_DATA_SUBDIR "EVA"
!endif

!macro EVA_UserContext
  ${If} $installMode == "all"
    SetShellVarContext current
  ${EndIf}
!macroend

!macro EVA_RestoreContext
  ${If} $installMode == "all"
    SetShellVarContext all
  ${EndIf}
!macroend

; ── Install marker ───────────────────────────────────────────────────────────
; Runs inside the installer's main Section on every install (fresh, reinstall,
; and silent auto-upgrade). Writes a small file to %APPDATA%\EVA\ so that
; main.js can detect the install on the next app launch and trigger the correct
; screen: Welcome (no existing user data) or What's New (returning user).
!macro customInstall
  !insertmacro EVA_UserContext
  CreateDirectory "$APPDATA\${EVA_DATA_SUBDIR}"
  FileOpen $0 "$APPDATA\${EVA_DATA_SUBDIR}\fresh-install" w
  FileWrite $0 "${VERSION}"
  FileClose $0
  ; Clear Chromium disk cache so updated static assets (CHANGELOG.md, etc.)
  ; are always fetched fresh after install. Cache lives in %APPDATA%\EVA\Cache\
  ; and persists across updates because user data is preserved. Without this,
  ; files served with the old max-age header stay cached for up to one year.
  ; Chromium recreates the directory automatically on next launch.
  RMDir /r "$APPDATA\${EVA_DATA_SUBDIR}\Cache"
  !insertmacro EVA_RestoreContext
!macroend

; ── Branding text override ────────────────────────────────────────────────────
; common.nsh sets: BrandingText "${PRODUCT_NAME} ${VERSION}"  (= "EVA 0.1.0")
; This macro is inserted after common.nsh so the last BrandingText wins.
!macro customHeader
  BrandingText "EVA ${VERSION}"
!macroend

!macro customUnInit
  Var /GLOBAL EVA_DelData
  Var /GLOBAL EVA_Checkbox

  StrCpy $EVA_DelData "0"          ; default = keep (safe for silent upgrades)
  !insertmacro EVA_UserContext     ; $APPDATA in the dialog text = the user's real folder

  ; Silent uninstall runs automatically when the new installer upgrades an
  ; existing install — skip the dialog and preserve user data.
  IfSilent EVA_SkipDialog

  StrCpy $EVA_DelData "1"          ; interactive default = delete (checkbox starts checked)

  nsDialogs::Create /NOUNLOAD 1018
  Pop $0
  ${If} $0 == error
    ; nsDialogs unavailable — fall back to a Yes/No MessageBox (default Yes)
    MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON1 \
      "Delete all EVA data?$\n$\nThis removes conversations and settings from:$\n    $APPDATA\${EVA_DATA_SUBDIR}$\n$\nand EVA's saved API keys from Windows Credential Manager.$\n$\nClick No to keep your data for a future reinstall." \
      IDNO EVA_NoDelete
    Goto EVA_DialogDone
    EVA_NoDelete:
      StrCpy $EVA_DelData "0"
    EVA_DialogDone:
  ${Else}
    ${NSD_CreateLabel} 0 0 100% 20u "Uninstall EVA — clean up user data"
    Pop $0

    ${NSD_CreateCheckbox} 0 28u 100% 14u "Delete all EVA data  (conversations, settings, vector database, saved API keys)"
    Pop $EVA_Checkbox
    ${NSD_SetState} $EVA_Checkbox ${BST_CHECKED}

    ${NSD_CreateLabel} 18u 48u 82% 40u \
      "Removes $APPDATA\${EVA_DATA_SUBDIR} (chat history, settings, RAG vector database) and EVA's saved API keys and sign-in key from Windows Credential Manager.$\nUncheck to keep your data for a future reinstall."
    Pop $0

    nsDialogs::Show

    ${NSD_GetState} $EVA_Checkbox $0
    ${If} $0 == ${BST_CHECKED}
      StrCpy $EVA_DelData "1"
    ${Else}
      StrCpy $EVA_DelData "0"
    ${EndIf}
  ${EndIf}
  EVA_SkipDialog:
  !insertmacro EVA_RestoreContext
!macroend

; ── Saved credentials (Windows Credential Manager) ──────────────────────────
; The backend keeps the signing key and every provider / MCP key under the keyring
; service "eva". Windows lists them as "<NAME>@eva" (plus possibly a bare "eva"),
; and the set is open-ended, so enumerate with cmdkey instead of using a fixed list.
; Only runs inside the "delete my data" branch below; silent upgrades never reach it.
; The service name is a define so a test build can aim at dummy entries
; (makensis /DEVA_CRED_SERVICE=evatest) without touching real ones.
; The match uses "LegacyGeneric:target=", which cmdkey prints in every Windows language.
!ifndef EVA_CRED_SERVICE
  !define EVA_CRED_SERVICE "eva"
!endif

!macro EVA_RemoveCredentials
  nsExec::Exec 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "cmdkey /list | ForEach-Object { if ($$_ -match $\'LegacyGeneric:target=(.+)$$$\') { $$t = $$Matches[1].Trim(); if ($$t -eq $\'${EVA_CRED_SERVICE}$\' -or $$t.EndsWith($\'@${EVA_CRED_SERVICE}$\')) { cmdkey /delete:$$t | Out-Null } } }"'
  Pop $0   ; exit code ignored: a failure here must never block the uninstall
!macroend

!macro customUnInstall
  ${If} $EVA_DelData == "1"
    !insertmacro EVA_UserContext
    RMDir /r "$APPDATA\${EVA_DATA_SUBDIR}"
    !insertmacro EVA_RemoveCredentials
    !insertmacro EVA_RestoreContext
  ${EndIf}
!macroend
