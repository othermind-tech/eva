'use strict'

/**
 * Post-processes the Vite renderer JS bundle with javascript-obfuscator.
 * OPTIONAL: build-electron.ps1 runs this only with -Obfuscate; default builds
 * ship the readable bundle. Run it after `vite build` and before
 * electron-builder packages the ASAR.
 *
 * Applies: control flow flattening, string array encoding, variable renaming.
 * The index.html + CSS are left untouched (CSS doesn't need obfuscation;
 * HTML is minimal glue). Only the main JS bundle is obfuscated. main.js and
 * the Python backend are never obfuscated.
 *
 */

const JavaScriptObfuscator = require('javascript-obfuscator')
const fs   = require('fs')
const path = require('path')

const distDir = path.resolve(__dirname, '../frontend/dist/assets')

if (!fs.existsSync(distDir)) {
  console.error('[obfuscate] dist/assets not found — run vite build first')
  process.exit(1)
}

const jsFiles = fs.readdirSync(distDir).filter(f => f.endsWith('.js'))

if (!jsFiles.length) {
  console.log('[obfuscate] No JS files found in dist/assets — nothing to do')
  process.exit(0)
}

console.log(`[obfuscate] Processing ${jsFiles.length} JS file(s) in ${distDir}`)

for (const file of jsFiles) {
  const filePath = path.join(distDir, file)
  const src      = fs.readFileSync(filePath, 'utf8')
  const sizeBefore = (src.length / 1024).toFixed(1)

  console.log(`  Obfuscating ${file} (${sizeBefore} KB)...`)

  const result = JavaScriptObfuscator.obfuscate(src, {
    compact:                     true,
    controlFlowFlattening:       true,
    controlFlowFlatteningThreshold: 0.5,
    deadCodeInjection:           false,   // keeps size reasonable
    stringArray:                 true,
    stringArrayEncoding:         ['rc4'],
    stringArrayThreshold:        0.75,
    rotateStringArray:           true,
    shuffleStringArray:          true,
    splitStrings:                false,
    identifierNamesGenerator:    'hexadecimal',
    renameGlobals:               false,   // safer for React globals
    sourceMap:                   false,   // no source maps in production
    selfDefending:               false,   // can break React in some cases
  })

  fs.writeFileSync(filePath, result.getObfuscatedCode(), 'utf8')
  const sizeAfter = (result.getObfuscatedCode().length / 1024).toFixed(1)
  console.log(`  Done: ${sizeBefore} KB → ${sizeAfter} KB`)
}

console.log('[obfuscate] Renderer obfuscation complete.')
