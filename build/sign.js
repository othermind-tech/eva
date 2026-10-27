// No-op code signing hook — skips signing for unsigned development builds
// Replace this with real signing logic for production distribution
exports.default = async function(configuration) {
  console.log(`[sign] Skipping signing for: ${configuration.path}`)
}
