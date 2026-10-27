'use strict'

/**
 * Electron Fuses — applied after electron-builder packs the app but before
 * the installer is created. Fuses are one-way binary flags baked into the exe.
 *
 */

const { flipFuses, FuseVersion, FuseV1Options } = require('@electron/fuses')
const path = require('path')
const fs   = require('fs')

module.exports = async function afterPack(context) {
  const { appOutDir, packager } = context
  const platform = packager.platform.nodeName  // 'win32', 'darwin', 'linux'

  let electronBin
  if (platform === 'win32') {
    electronBin = path.join(appOutDir, 'EVA.exe')
  } else if (platform === 'darwin') {
    electronBin = path.join(appOutDir, 'EVA.app', 'Contents', 'MacOS', 'EVA')
  } else {
    electronBin = path.join(appOutDir, 'eva')
  }

  if (!fs.existsSync(electronBin)) {
    console.warn(`[fuses] Binary not found at ${electronBin} — skipping fuses`)
    return
  }

  console.log(`[fuses] Applying Electron Fuses to: ${electronBin}`)

  await flipFuses(electronBin, {
    version: FuseVersion.V1,
    [FuseV1Options.RunAsNode]:                        false, // Block using EVA.exe as a Node interpreter
    [FuseV1Options.EnableNodeCliInspectArguments]:    false, // Block --inspect debugger
    [FuseV1Options.EnableEmbeddedAsarIntegrityValidation]: true,  // Verify ASAR hash on startup
    [FuseV1Options.OnlyLoadAppFromAsar]:              true,  // Block loading code from outside ASAR
  })

  console.log('[fuses] Fuses applied successfully.')
}
