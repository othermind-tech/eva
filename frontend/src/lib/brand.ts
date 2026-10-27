// Edition-specific wording, in ONE place. WelcomePanel.tsx reads everything branded from here.
// Keep the shape (the Brand interface) stable; change values freely.
//
// Edition-neutral wording (product description, feature copy) stays in the components.
export interface Brand {
  /** alt text of the hero logo */
  logoAlt: string
  /** badge / footer label, shown as "<label> · V1.2.3" */
  editionLabel: string
  /** product expansion shown in the intro sentence; null to omit it ("EVA is a provider-agnostic...") */
  tagline: string | null
  /** bold lead-in of the legal notice, including its trailing space */
  noticeTitle: string
  /** body of the legal notice */
  noticeBody: string
  /** copyright / licence lead of the footer line; the edition label and version follow it */
  footerLead: string
}

export const BRAND: Brand = {
  logoAlt: 'EVA',
  editionLabel: 'Beta',
  tagline: null,
  noticeTitle: 'Open-source beta. ',
  noticeBody:
    'EVA is open-source software released under the Apache License 2.0. This is a pre-1.0 beta: features and behaviour may change between versions, and you may run into bugs. It is provided as-is, without warranty of any kind; see the LICENSE file for the full terms.',
  footerLead: 'EVA · Open source under Apache-2.0',
}
