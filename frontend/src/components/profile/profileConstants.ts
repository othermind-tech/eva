// ─── User Profile Modal ───────────────────────────────────────────────────────
export const TZ_LIST: string[] = (() => {
  try {
    // Use the browser's full IANA list when available (Chrome 99+, Firefox 86+)
    return (Intl as unknown as { supportedValuesOf: (k: string) => string[] })
      .supportedValuesOf('timeZone')
  } catch {
    // Curated fallback
    return [
      'UTC',
      'America/New_York','America/Chicago','America/Denver','America/Los_Angeles',
      'America/Toronto','America/Vancouver','America/Sao_Paulo','America/Argentina/Buenos_Aires',
      'Europe/London','Europe/Dublin','Europe/Paris','Europe/Berlin','Europe/Rome',
      'Europe/Amsterdam','Europe/Stockholm','Europe/Moscow',
      'Africa/Cairo','Africa/Nairobi','Africa/Johannesburg',
      'Asia/Dubai','Asia/Karachi','Asia/Kolkata','Asia/Dhaka',
      'Asia/Bangkok','Asia/Singapore','Asia/Hong_Kong','Asia/Tokyo',
      'Asia/Seoul','Asia/Shanghai',
      'Australia/Perth','Australia/Brisbane','Australia/Sydney','Australia/Auckland',
      'Pacific/Auckland','Pacific/Honolulu',
    ]
  }
})()

// ─── Language list (BCP-47) ───────────────────────────────────────────────────
export const LANGUAGE_LIST = [
  { code: 'af',    label: 'Afrikaans'              },
  { code: 'ar',    label: 'Arabic'                 },
  { code: 'bn',    label: 'Bengali'                },
  { code: 'zh-CN', label: 'Chinese (Simplified)'   },
  { code: 'zh-TW', label: 'Chinese (Traditional)'  },
  { code: 'hr',    label: 'Croatian'               },
  { code: 'cs',    label: 'Czech'                  },
  { code: 'da',    label: 'Danish'                 },
  { code: 'nl',    label: 'Dutch'                  },
  { code: 'en',    label: 'English'                },
  { code: 'en-GB', label: 'English (UK)'           },
  { code: 'en-US', label: 'English (US)'           },
  { code: 'en-IN', label: 'English (India)'        },
  { code: 'et',    label: 'Estonian'               },
  { code: 'fi',    label: 'Finnish'                },
  { code: 'fr',    label: 'French'                 },
  { code: 'de',    label: 'German'                 },
  { code: 'el',    label: 'Greek'                  },
  { code: 'gu',    label: 'Gujarati'               },
  { code: 'he',    label: 'Hebrew'                 },
  { code: 'hi',    label: 'Hindi'                  },
  { code: 'hu',    label: 'Hungarian'              },
  { code: 'id',    label: 'Indonesian'             },
  { code: 'it',    label: 'Italian'                },
  { code: 'ja',    label: 'Japanese'               },
  { code: 'kn',    label: 'Kannada'                },
  { code: 'ko',    label: 'Korean'                 },
  { code: 'lv',    label: 'Latvian'                },
  { code: 'lt',    label: 'Lithuanian'             },
  { code: 'ms',    label: 'Malay'                  },
  { code: 'ml',    label: 'Malayalam'              },
  { code: 'mr',    label: 'Marathi'                },
  { code: 'no',    label: 'Norwegian'              },
  { code: 'fa',    label: 'Persian (Farsi)'        },
  { code: 'pl',    label: 'Polish'                 },
  { code: 'pt-BR', label: 'Portuguese (Brazil)'    },
  { code: 'pt',    label: 'Portuguese (Portugal)'  },
  { code: 'pa',    label: 'Punjabi'                },
  { code: 'ro',    label: 'Romanian'               },
  { code: 'ru',    label: 'Russian'                },
  { code: 'sk',    label: 'Slovak'                 },
  { code: 'sl',    label: 'Slovenian'              },
  { code: 'es',    label: 'Spanish'                },
  { code: 'es-MX', label: 'Spanish (Mexico)'       },
  { code: 'sv',    label: 'Swedish'                },
  { code: 'ta',    label: 'Tamil'                  },
  { code: 'te',    label: 'Telugu'                 },
  { code: 'th',    label: 'Thai'                   },
  { code: 'tr',    label: 'Turkish'                },
  { code: 'uk',    label: 'Ukrainian'              },
  { code: 'ur',    label: 'Urdu'                   },
  { code: 'vi',    label: 'Vietnamese'             },
]

// ─── Shared input style for profile sub-components ────────────────────────────
export const PROFILE_INPUT: React.CSSProperties = {
  width: '100%', padding: '10px 13px', boxSizing: 'border-box',
  background: 'var(--bg-surface2)', border: '1px solid var(--border-dim)',
  borderRadius: 9, color: 'var(--text-primary)', fontSize: 13.5,
  outline: 'none', transition: 'border-color 150ms ease',
}
export const profileFocus = (e: React.FocusEvent<HTMLInputElement>) =>
  (e.currentTarget.style.borderColor = 'rgba(0,201,255,0.4)')
export const profileBlur  = (e: React.FocusEvent<HTMLInputElement>) =>
  (e.currentTarget.style.borderColor = 'var(--border-dim)')
