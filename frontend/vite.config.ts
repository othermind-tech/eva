import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'
import { viteStaticCopy } from 'vite-plugin-static-copy'

// Every @fontsource @font-face lists a .woff after the .woff2 as a fallback for browsers without
// woff2. Chromium (so Electron) always takes the woff2 and never requests the .woff, but Vite still
// copies every .woff into dist - about 630 KB of files nothing loads. Drop the fallback at the source.
// Only the `, url(x.woff) format('woff')` part is removed; the woff2 src is untouched.
function dropWoffFallback(): Plugin {
  return {
    name: 'eva-drop-woff-fallback',
    enforce: 'pre',
    transform(code, id) {
      if (!/[\\/]node_modules[\\/]@fontsource[\\/].+\.css$/.test(id.split('?')[0])) return null
      return { code: code.replace(/,\s*url\([^)]*\.woff\)\s*format\(['"]woff['"]\)/g, ''), map: null }
    },
  }
}

export default defineConfig({
  plugins: [
    dropWoffFallback(),
    react(),
    tailwindcss(),
    viteStaticCopy({
      targets: [
        { src: '../docs/**/*', dest: '.' },  // subdirectory files
        { src: '../docs/*.*',  dest: '.' },  // root-level files (CHANGELOG.md etc.)
      ],
    }),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
