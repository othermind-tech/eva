// Self-hosted fonts (SIL OFL 1.1, bundled by Vite) - replaces the Google Fonts <link> tags so the app
// makes no request to fonts.* hosts and renders offline. Static packages (not -variable) keep the family
// names 'Inter' and 'JetBrains Mono' that index.css and the inline styles already use.
// Imported from TS rather than CSS @import: an @import in index.css conflicts with Tailwind's ordering.
//
// Import the combined per-weight files (400.css), NOT the per-subset ones (latin-400.css). Only the
// combined files carry a unicode-range on each subset, so the browser requests a subset file only when
// the page has characters in its range - an English session fetches just the latin files. The
// per-subset files have no unicode-range at all (every face claims U+0-10FFFF), and importing them
// made the login page fetch all five subsets, Cyrillic / Greek / Vietnamese included (checked in Chromium).
// The .woff fallbacks these files also list are stripped at build time (dropWoffFallback in vite.config.ts).

// Inter - UI text
import '@fontsource/inter/300.css'
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/inter/700.css'

// JetBrains Mono - code, IDs, metrics
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/500.css'
