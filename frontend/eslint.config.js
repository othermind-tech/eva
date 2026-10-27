import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      // A leading underscore means "intentionally unused" (a callback parameter we must accept, or a
      // field dropped by rest-destructuring); the codebase already uses that convention.
      '@typescript-eslint/no-unused-vars': ['error', {
        argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_',
        ignoreRestSiblings: true,
      }],
      // Two rules are warnings, not errors, on purpose; both stay visible in `npm run lint` output.
      //  - set-state-in-effect: a strict, compiler-oriented check. The flagged effects are ordinary patterns
      //    (start a fetch and reset its state, sync a prop into form state, poll a health endpoint); rewriting
      //    them to satisfy the rule risks behaviour changes for no gain. Refactor them one at a time, with tests.
      //  - only-export-components: only affects dev-server fast refresh (a file exporting both components and
      //    helpers does a full reload instead of a hot swap); it has no effect on the built app.
      'react-hooks/set-state-in-effect': 'warn',
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
    },
  },
])
