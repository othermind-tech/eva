# Third-Party Licenses

EVA is licensed under the Apache License 2.0 (see [`LICENSE`](LICENSE)). It depends on, and the packaged
desktop app redistributes, third-party software under their own licenses, listed below.

This list is generated from the dependency lock files and was last generated on 2026-10-27.
Python versions are those pinned in `backend/requirements.lock`; npm versions are those in
`frontend/package-lock.json` and `build/package-lock.json`. The full text of each license is included in the
corresponding package's own distribution (for Python, in the package's `dist-info` directory; for npm, in
`node_modules/<package>`).

**Copyleft note.** Apart from the components bundled inside Chromium (see section 3), no package listed in the
tables below uses a strong-copyleft license (GPL or AGPL). 3 Python packages (certifi, orjson, tqdm) are under
the Mozilla Public License 2.0 (MPL-2.0), a file-level license; EVA uses them unmodified. Development-only tools
that are not shipped (for example PyInstaller, which is GPL-2.0-or-later with an exception that allows it to be
used to build and distribute programs, and the `lightningcss` build tool) are not listed in the runtime tables.

## 1. Python backend (169 packages)

| Package | Version | License |
|---|---|---|
| aiohappyeyeballs | 2.7.1 | PSF-2.0 |
| aiohttp | 3.14.5 | Apache-2.0 AND MIT |
| aiohttp-retry | 2.9.1 | MIT |
| aiosignal | 1.4.0 | Apache 2.0 |
| aiosqlite | 0.22.1 | MIT License |
| alembic | 1.20.0 | MIT |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anthropic | 0.125.0 | MIT |
| anyio | 4.15.1 | MIT |
| asyncpg | 0.32.0 | Apache-2.0 |
| attrs | 26.1.0 | MIT |
| backports.tarfile | 1.2.0 | MIT License |
| bcrypt | 4.3.0 | Apache-2.0 |
| blis | 1.3.3 | BSD |
| boto3 | 1.43.115 | Apache-2.0 |
| botocore | 1.43.115 | Apache-2.0 |
| build | 1.6.1 | MIT |
| catalogue | 2.0.10 | MIT |
| certifi | 2026.10.19 | MPL-2.0 |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| chromadb | 1.5.9 | Apache Software License |
| click | 8.5.0 | BSD-3-Clause |
| cloudpathlib | 0.26.0 | MIT License |
| colorama | 0.4.6 | BSD License |
| confection | 1.3.3 | MIT |
| cryptography | 48.0.1 | Apache-2.0 OR BSD-3-Clause |
| cymem | 2.0.13 | MIT |
| ddgs | 9.16.0 | MIT |
| distro | 1.9.0 | Apache License, Version 2.0 |
| dnspython | 2.9.0 | ISC |
| docstring_parser | 0.18.0 | MIT |
| docx2txt | 0.9 | MIT |
| durationpy | 0.11 | MIT |
| ecdsa | 0.19.2 | MIT |
| email-validator | 2.3.0 | Unlicense |
| en_core_web_sm | 3.8.0 | MIT |
| fastapi | 0.153.0 | MIT |
| fastuuid | 0.14.0 | BSD License |
| filelock | 3.32.7 | MIT |
| flatbuffers | 25.12.19 | Apache 2.0 |
| frozenlist | 1.8.0 | Apache-2.0 |
| fsspec | 2026.9.0 | BSD-3-Clause |
| googleapis-common-protos | 1.75.5 | Apache-2.0 |
| greenlet | 3.5.6 | MIT AND PSF-2.0 |
| groq | 1.7.0 | Apache-2.0 |
| grpcio | 1.84.0 | Apache-2.0 |
| h11 | 0.16.0 | MIT |
| h2 | 4.4.1 | MIT |
| hf-xet | 1.7.0 | Apache-2.0 |
| hpack | 4.2.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpcore2 | 2.15.1 | BSD-3-Clause |
| httptools | 0.9.0 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| httpx2 | 2.15.1 | BSD-3-Clause |
| huggingface_hub | 1.33.0 | Apache-2.0 |
| hyperframe | 6.1.0 | MIT License |
| idna | 3.20 | BSD-3-Clause |
| importlib_metadata | 8.9.0 | Apache-2.0 |
| importlib_resources | 7.1.0 | Apache-2.0 |
| jaraco.classes | 3.4.0 | MIT License |
| jaraco.context | 6.1.2 | MIT |
| jaraco.functools | 4.6.0 | MIT |
| Jinja2 | 3.1.6 | BSD License |
| jiter | 0.17.0 | MIT |
| jmespath | 1.1.0 | MIT |
| jsonschema | 4.26.0 | MIT |
| jsonschema-specifications | 2025.9.1 | MIT |
| keyring | 25.7.0 | MIT |
| kubernetes | 37.0.1 | Apache-2.0 |
| litellm | 1.106.0 | MIT |
| lxml | 6.1.3 | BSD-3-Clause |
| Mako | 1.4.3 | MIT |
| markdown-it-py | 4.2.0 | MIT License |
| MarkupSafe | 3.0.4 | BSD-3-Clause |
| mcp | 2.3.0 | MIT |
| mcp-types | 2.3.0 | MIT |
| mdurl | 0.1.2 | MIT License |
| mmh3 | 5.3.1 | MIT License |
| more-itertools | 11.2.1 | MIT |
| multidict | 7.1.0 | Apache-2.0 |
| murmurhash | 1.0.15 | MIT |
| numpy | 2.4.2 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| oauthlib | 4.0.0 | BSD-3-Clause |
| ollama | 0.6.3 | MIT |
| onnxruntime | 1.31.0 | MIT License |
| openai | 2.54.0 | Apache-2.0 |
| opentelemetry-api | 1.45.1 | Apache-2.0 |
| opentelemetry-exporter-otlp-common | 0.66b1 | Apache-2.0 |
| opentelemetry-exporter-otlp-proto-common | 1.45.1 | Apache-2.0 |
| opentelemetry-exporter-otlp-proto-grpc | 1.45.1 | Apache-2.0 |
| opentelemetry-proto | 1.45.1 | Apache-2.0 |
| opentelemetry-sdk | 1.45.1 | Apache-2.0 |
| opentelemetry-semantic-conventions | 0.66b1 | Apache-2.0 |
| orjson | 3.13.0 | MPL-2.0 AND (Apache-2.0 OR MIT) |
| overrides | 7.7.0 | Apache License, Version 2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pandas | 3.0.6 | BSD License |
| passlib | 1.7.4 | BSD |
| phonenumbers | 9.0.41 | Apache-2.0 |
| preshed | 3.0.13 | MIT |
| presidio_analyzer | 2.2.364 | MIT |
| presidio_anonymizer | 2.2.364 | MIT |
| primp | 2.0.1 | MIT License |
| propcache | 0.5.4 | Apache-2.0 |
| protobuf | 7.36.2 | 3-Clause BSD License |
| psutil | 7.2.2 | BSD-3-Clause |
| pyasn1 | 0.6.4 | BSD-2-Clause |
| pybase64 | 1.5.1 | BSD-2-Clause |
| pycparser | 3.11 | BSD-3-Clause |
| pydantic | 2.14.1 | MIT |
| pydantic-settings | 2.16.1 | MIT |
| pydantic_core | 2.50.1 | MIT |
| Pygments | 2.21.0 | BSD-2-Clause |
| PyJWT | 2.15.1 | MIT |
| pypdf | 6.21.1 | BSD-3-Clause |
| PyPika | 0.51.1 | Apache License Version 2.0 |
| pyproject_hooks | 1.3.3 | MIT |
| python-dateutil | 2.9.0.post0 | Dual License |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| python-jose | 3.5.0 | MIT |
| python-multipart | 0.0.32 | Apache-2.0 |
| pywin32 | 312 | PSF |
| pywin32-ctypes | 0.2.3 | BSD-3-Clause |
| PyYAML | 6.0.3 | MIT |
| referencing | 0.37.0 | MIT |
| regex | 2026.9.29 | Apache-2.0 AND CNRI-Python |
| requests | 2.34.2 | Apache-2.0 |
| requests-file | 3.0.1 | Apache 2.0 |
| requests-oauthlib | 2.0.0 | ISC |
| rich | 15.0.0 | MIT |
| rpds-py | 2026.9.1 | MIT |
| rsa | 4.9.1 | Apache-2.0 |
| s3transfer | 0.19.3 | Apache License 2.0 |
| setuptools | 84.2.0 | MIT |
| shellingham | 1.5.4 | ISC License |
| six | 1.17.0 | MIT |
| smart_open | 8.0.4 | MIT License |
| sniffio | 1.3.1 | MIT OR Apache-2.0 |
| spacy | 3.8.16 | MIT |
| spacy-legacy | 3.0.12 | MIT |
| spacy-loggers | 1.0.5 | MIT |
| SQLAlchemy | 2.1.5 | MIT |
| srsly | 2.5.4 | MIT |
| sse-starlette | 3.5.0 | BSD-3-Clause |
| starlette | 1.9.0 | BSD-3-Clause |
| tenacity | 9.2.1 | Apache-2.0 |
| thinc | 8.3.13 | MIT |
| tiktoken | 0.14.0 | MIT License Copyright (c) 2022 OpenAI, Shantanu Jain Permi |
| tldextract | 5.4.0 | BSD-3-Clause |
| tokenizers | 0.23.3 | Apache Software License |
| tqdm | 4.70.1 | MPL-2.0 AND MIT |
| truststore | 0.10.5 | MIT |
| typer | 0.27.3 | MIT |
| typing-inspection | 0.4.4 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| tzdata | 2026.5 | Apache-2.0 |
| urllib3 | 2.8.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| wasabi | 1.1.3 | MIT |
| watchfiles | 1.3.0 | MIT |
| weasel | 1.0.0 | MIT |
| websocket-client | 1.9.2 | Apache-2.0 |
| websockets | 17.3 | BSD-3-Clause |
| wrapt | 2.5.1 | BSD-2-Clause |
| yarl | 1.25.1 | Apache-2.0 |
| zipp | 4.1.1 | MIT |

## 2. Frontend, bundled into the app (212 npm packages)

| Package | Version | License |
|---|---|---|
| @floating-ui/core | 1.7.5 | MIT |
| @floating-ui/dom | 1.7.6 | MIT |
| @floating-ui/react-dom | 2.1.8 | MIT |
| @floating-ui/utils | 0.2.11 | MIT |
| @fontsource/inter | 5.3.0 | OFL-1.1 |
| @fontsource/jetbrains-mono | 5.3.0 | OFL-1.1 |
| @radix-ui/number | 1.1.1 | MIT |
| @radix-ui/primitive | 1.1.3 | MIT |
| @radix-ui/react-arrow | 1.1.7 | MIT |
| @radix-ui/react-avatar | 1.1.11 | MIT |
| @radix-ui/react-checkbox | 1.3.3 | MIT |
| @radix-ui/react-collection | 1.1.7 | MIT |
| @radix-ui/react-compose-refs | 1.1.2 | MIT |
| @radix-ui/react-context | 1.1.2 | MIT |
| @radix-ui/react-context | 1.1.3 | MIT |
| @radix-ui/react-dialog | 1.1.15 | MIT |
| @radix-ui/react-direction | 1.1.1 | MIT |
| @radix-ui/react-dismissable-layer | 1.1.11 | MIT |
| @radix-ui/react-dropdown-menu | 2.1.16 | MIT |
| @radix-ui/react-focus-guards | 1.1.3 | MIT |
| @radix-ui/react-focus-scope | 1.1.7 | MIT |
| @radix-ui/react-id | 1.1.1 | MIT |
| @radix-ui/react-label | 2.1.8 | MIT |
| @radix-ui/react-menu | 2.1.16 | MIT |
| @radix-ui/react-popover | 1.1.15 | MIT |
| @radix-ui/react-popper | 1.2.8 | MIT |
| @radix-ui/react-portal | 1.1.9 | MIT |
| @radix-ui/react-presence | 1.1.5 | MIT |
| @radix-ui/react-primitive | 2.1.3 | MIT |
| @radix-ui/react-primitive | 2.1.4 | MIT |
| @radix-ui/react-progress | 1.1.8 | MIT |
| @radix-ui/react-roving-focus | 1.1.11 | MIT |
| @radix-ui/react-scroll-area | 1.2.10 | MIT |
| @radix-ui/react-select | 2.2.6 | MIT |
| @radix-ui/react-separator | 1.1.8 | MIT |
| @radix-ui/react-slot | 1.2.3 | MIT |
| @radix-ui/react-slot | 1.2.4 | MIT |
| @radix-ui/react-switch | 1.2.6 | MIT |
| @radix-ui/react-tabs | 1.1.13 | MIT |
| @radix-ui/react-toast | 1.2.15 | MIT |
| @radix-ui/react-tooltip | 1.2.8 | MIT |
| @radix-ui/react-use-callback-ref | 1.1.1 | MIT |
| @radix-ui/react-use-controllable-state | 1.2.2 | MIT |
| @radix-ui/react-use-effect-event | 0.0.2 | MIT |
| @radix-ui/react-use-escape-keydown | 1.1.1 | MIT |
| @radix-ui/react-use-is-hydrated | 0.1.0 | MIT |
| @radix-ui/react-use-layout-effect | 1.1.1 | MIT |
| @radix-ui/react-use-previous | 1.1.1 | MIT |
| @radix-ui/react-use-rect | 1.1.1 | MIT |
| @radix-ui/react-use-size | 1.1.1 | MIT |
| @radix-ui/react-visually-hidden | 1.2.3 | MIT |
| @radix-ui/rect | 1.1.1 | MIT |
| @tanstack/query-core | 5.90.20 | MIT |
| @tanstack/react-query | 5.90.21 | MIT |
| @types/debug | 4.1.12 | MIT |
| @types/estree | 1.0.8 | MIT |
| @types/estree-jsx | 1.0.5 | MIT |
| @types/hast | 3.0.4 | MIT |
| @types/mdast | 4.0.4 | MIT |
| @types/ms | 2.1.0 | MIT |
| @types/react | 19.2.14 | MIT |
| @types/react-dom | 19.2.3 | MIT |
| @types/unist | 3.0.3 | MIT |
| @types/unist | 2.0.11 | MIT |
| @ungap/structured-clone | 1.3.0 | ISC |
| aria-hidden | 1.2.6 | MIT |
| asynckit | 0.4.0 | MIT |
| axios | 1.13.6 | MIT |
| bail | 2.0.2 | MIT |
| call-bind-apply-helpers | 1.0.2 | MIT |
| ccount | 2.0.1 | MIT |
| character-entities | 2.0.2 | MIT |
| character-entities-html4 | 2.1.0 | MIT |
| character-entities-legacy | 3.0.0 | MIT |
| character-reference-invalid | 2.0.1 | MIT |
| class-variance-authority | 0.7.1 | Apache-2.0 |
| clsx | 2.1.1 | MIT |
| combined-stream | 1.0.8 | MIT |
| comma-separated-tokens | 2.0.3 | MIT |
| cookie | 1.1.1 | MIT |
| csstype | 3.2.3 | MIT |
| date-fns | 4.1.0 | MIT |
| debug | 4.4.3 | MIT |
| decode-named-character-reference | 1.3.0 | MIT |
| delayed-stream | 1.0.0 | MIT |
| dequal | 2.0.3 | MIT |
| detect-node-es | 1.1.0 | MIT |
| devlop | 1.1.0 | MIT |
| dunder-proto | 1.0.1 | MIT |
| es-define-property | 1.0.1 | MIT |
| es-errors | 1.3.0 | MIT |
| es-object-atoms | 1.1.1 | MIT |
| es-set-tostringtag | 2.1.0 | MIT |
| escape-string-regexp | 5.0.0 | MIT |
| estree-util-is-identifier-name | 3.0.0 | MIT |
| extend | 3.0.2 | MIT |
| follow-redirects | 1.15.11 | MIT |
| form-data | 4.0.5 | MIT |
| framer-motion | 12.35.2 | MIT |
| function-bind | 1.1.2 | MIT |
| get-intrinsic | 1.3.0 | MIT |
| get-nonce | 1.0.1 | MIT |
| get-proto | 1.0.1 | MIT |
| gopd | 1.2.0 | MIT |
| has-symbols | 1.1.0 | MIT |
| has-tostringtag | 1.0.2 | MIT |
| hasown | 2.0.2 | MIT |
| hast-util-is-element | 3.0.0 | MIT |
| hast-util-to-jsx-runtime | 2.3.6 | MIT |
| hast-util-to-text | 4.0.2 | MIT |
| hast-util-whitespace | 3.0.0 | MIT |
| highlight.js | 11.11.1 | BSD-3-Clause |
| html-url-attributes | 3.0.1 | MIT |
| inline-style-parser | 0.2.7 | MIT |
| is-alphabetical | 2.0.1 | MIT |
| is-alphanumerical | 2.0.1 | MIT |
| is-decimal | 2.0.1 | MIT |
| is-hexadecimal | 2.0.1 | MIT |
| is-plain-obj | 4.1.0 | MIT |
| longest-streak | 3.1.0 | MIT |
| lowlight | 3.3.0 | MIT |
| lucide-react | 0.577.0 | ISC |
| markdown-table | 3.0.4 | MIT |
| math-intrinsics | 1.1.0 | MIT |
| mdast-util-find-and-replace | 3.0.2 | MIT |
| mdast-util-from-markdown | 2.0.3 | MIT |
| mdast-util-gfm | 3.1.0 | MIT |
| mdast-util-gfm-autolink-literal | 2.0.1 | MIT |
| mdast-util-gfm-footnote | 2.1.0 | MIT |
| mdast-util-gfm-strikethrough | 2.0.0 | MIT |
| mdast-util-gfm-table | 2.0.0 | MIT |
| mdast-util-gfm-task-list-item | 2.0.0 | MIT |
| mdast-util-mdx-expression | 2.0.1 | MIT |
| mdast-util-mdx-jsx | 3.2.0 | MIT |
| mdast-util-mdxjs-esm | 2.0.1 | MIT |
| mdast-util-phrasing | 4.1.0 | MIT |
| mdast-util-to-hast | 13.2.1 | MIT |
| mdast-util-to-markdown | 2.1.2 | MIT |
| mdast-util-to-string | 4.0.0 | MIT |
| micromark | 4.0.2 | MIT |
| micromark-core-commonmark | 2.0.3 | MIT |
| micromark-extension-gfm | 3.0.0 | MIT |
| micromark-extension-gfm-autolink-literal | 2.1.0 | MIT |
| micromark-extension-gfm-footnote | 2.1.0 | MIT |
| micromark-extension-gfm-strikethrough | 2.1.0 | MIT |
| micromark-extension-gfm-table | 2.1.1 | MIT |
| micromark-extension-gfm-tagfilter | 2.0.0 | MIT |
| micromark-extension-gfm-task-list-item | 2.1.0 | MIT |
| micromark-factory-destination | 2.0.1 | MIT |
| micromark-factory-label | 2.0.1 | MIT |
| micromark-factory-space | 2.0.1 | MIT |
| micromark-factory-title | 2.0.1 | MIT |
| micromark-factory-whitespace | 2.0.1 | MIT |
| micromark-util-character | 2.1.1 | MIT |
| micromark-util-chunked | 2.0.1 | MIT |
| micromark-util-classify-character | 2.0.1 | MIT |
| micromark-util-combine-extensions | 2.0.1 | MIT |
| micromark-util-decode-numeric-character-reference | 2.0.2 | MIT |
| micromark-util-decode-string | 2.0.1 | MIT |
| micromark-util-encode | 2.0.1 | MIT |
| micromark-util-html-tag-name | 2.0.1 | MIT |
| micromark-util-normalize-identifier | 2.0.1 | MIT |
| micromark-util-resolve-all | 2.0.1 | MIT |
| micromark-util-sanitize-uri | 2.0.1 | MIT |
| micromark-util-subtokenize | 2.1.0 | MIT |
| micromark-util-symbol | 2.0.1 | MIT |
| micromark-util-types | 2.0.2 | MIT |
| mime-db | 1.52.0 | MIT |
| mime-types | 2.1.35 | MIT |
| motion-dom | 12.35.2 | MIT |
| motion-utils | 12.29.2 | MIT |
| ms | 2.1.3 | MIT |
| parse-entities | 4.0.2 | MIT |
| property-information | 7.1.0 | MIT |
| proxy-from-env | 1.1.0 | MIT |
| react | 19.2.4 | MIT |
| react-dom | 19.2.4 | MIT |
| react-markdown | 10.1.0 | MIT |
| react-remove-scroll | 2.7.2 | MIT |
| react-remove-scroll-bar | 2.3.8 | MIT |
| react-router | 7.13.1 | MIT |
| react-router-dom | 7.13.1 | MIT |
| react-style-singleton | 2.2.3 | MIT |
| rehype-highlight | 7.0.2 | MIT |
| remark-gfm | 4.0.1 | MIT |
| remark-parse | 11.0.0 | MIT |
| remark-rehype | 11.1.2 | MIT |
| remark-stringify | 11.0.0 | MIT |
| scheduler | 0.27.0 | MIT |
| set-cookie-parser | 2.7.2 | MIT |
| space-separated-tokens | 2.0.2 | MIT |
| stringify-entities | 4.0.4 | MIT |
| style-to-js | 1.1.21 | MIT |
| style-to-object | 1.0.14 | MIT |
| tailwind-merge | 3.5.0 | MIT |
| trim-lines | 3.0.1 | MIT |
| trough | 2.2.0 | MIT |
| tslib | 2.8.1 | 0BSD |
| unified | 11.0.5 | MIT |
| unist-util-find-after | 5.0.0 | MIT |
| unist-util-is | 6.0.1 | MIT |
| unist-util-position | 5.0.0 | MIT |
| unist-util-stringify-position | 4.0.0 | MIT |
| unist-util-visit | 5.1.0 | MIT |
| unist-util-visit-parents | 6.0.2 | MIT |
| use-callback-ref | 1.3.3 | MIT |
| use-sidecar | 1.1.3 | MIT |
| use-sync-external-store | 1.6.0 | MIT |
| vfile | 6.0.3 | MIT |
| vfile-message | 4.0.3 | MIT |
| zustand | 5.0.11 | MIT |
| zwitch | 2.0.4 | MIT |

## 3. Desktop shell (Electron)

Runtime npm dependencies of the Electron shell (17 packages):

| Package | Version | License |
|---|---|---|
| argparse | 2.0.1 | Python-2.0 |
| builder-util-runtime | 9.7.0 | MIT |
| debug | 4.4.3 | MIT |
| electron-updater | 6.8.9 | MIT |
| fs-extra | 10.1.0 | MIT |
| graceful-fs | 4.2.11 | ISC |
| js-yaml | 4.2.0 | MIT |
| jsonfile | 6.2.1 | MIT |
| lazy-val | 1.0.5 | MIT |
| lodash.escaperegexp | 4.1.2 | MIT |
| lodash.isequal | 4.5.0 | MIT |
| ms | 2.1.3 | MIT |
| sax | 1.6.0 | BlueOak-1.0.0 |
| semver | 7.7.4 | ISC |
| tiny-typed-emitter | 2.1.0 | MIT |
| universalify | 2.0.1 | MIT |
| ws | 8.21.3 | MIT |

Additionally:

- **Electron** (MIT) and the **Chromium** and **FFmpeg** components it contains are redistributed in the
  installer. Their license notices (`LICENSE.electron.txt` and `LICENSES.chromium.html`) are generated by
  electron-builder and ship next to `EVA.exe`.
- **ws** (MIT) is vendored under `build/ws-vendor/` together with its license file.
- **Microsoft Visual C++ runtime** (`msvcp140.dll`, `vcruntime140.dll`, `vcruntime140_1.dll`) is bundled
  application-local with the backend. These files are copied from the build machine's Visual C++ Redistributable
  and are redistributed under Microsoft's Visual C++ Redistributable license terms.

## 4. Models and data fetched at build or run time

These are not stored in this repository.

| Component | License | How it is obtained |
|---|---|---|
| sentence-transformers/all-MiniLM-L6-v2 (ONNX weights) | Apache-2.0 | Downloaded from the Hugging Face Hub when the backend is built and bundled into the installer. When EVA is run from source, it is downloaded on first use of the Knowledge Base. |
| spaCy `en_core_web_sm` | MIT | Installed from the hash-pinned URL in `backend/requirements.lock` |
| tiktoken encodings (`cl100k_base`, `o200k_base`) | MIT (tiktoken) | Generated by the backend build script |
| `@playwright/mcp` (default browser-testing MCP server) | Apache-2.0 | Fetched by `npx` when the Playwright MCP server is first enabled |
| LiteLLM model price and capability data | MIT (LiteLLM) | Public JSON data file fetched from GitHub at startup |

## 5. Fonts

**Inter** and **JetBrains Mono** (both licensed under the SIL Open Font License 1.1) are bundled with the app
through the `@fontsource/inter` and `@fontsource/jetbrains-mono` packages listed in section 2, which ship the font
license files. The app does not request fonts from any third-party server.

## 6. Source code adapted from other projects

Parts of EVA are adapted from the following open-source project. Its license notice is reproduced here as that
license requires.

**OpenHands Software Agent SDK** (https://github.com/OpenHands/software-agent-sdk), MIT License.

| EVA file | Adapted from |
|---|---|
| `backend/core/skills/trigger.py` | `openhands-sdk/openhands/sdk/skills/trigger.py` and `skill.py` (skill trigger types and matchers) |
| `backend/core/mcp/credentials.py` | `openhands-sdk/openhands/sdk/utils/redact.py` (secret-key detection patterns) |

```
MIT License

Copyright (c) 2026 OpenHands contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
