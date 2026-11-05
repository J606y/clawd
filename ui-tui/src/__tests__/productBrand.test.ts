import { describe, expect, it } from 'vitest'

import { logo, LOGO_WIDTH } from '../banner.js'
import { displayBrandCopy, displayBrandName, PRODUCT_COMMAND, PRODUCT_NAME } from '../productDisplay.js'
import { DARK_THEME } from '../theme.js'

describe('clawd product display', () => {
  it('uses clawd as the default brand and command prompt', () => {
    expect(PRODUCT_NAME).toBe('clawd')
    expect(PRODUCT_COMMAND).toBe('clawd')
    expect(DARK_THEME.brand.name).toBe('clawd')
  })

  it('maps only the built-in Hermes product name, not a custom skin', () => {
    expect(displayBrandName(undefined)).toBe('clawd')
    expect(displayBrandName('Hermes')).toBe('clawd')
    expect(displayBrandName('Hermes Agent')).toBe('clawd')
    expect(displayBrandName('Ares Agent')).toBe('Ares Agent')
    expect(displayBrandName('TestBot')).toBe('TestBot')
    expect(displayBrandCopy('Welcome to Hermes Agent! Type your message or /help for commands.', 'fallback')).toBe(
      'Welcome to clawd! Type your message or /help for commands.'
    )
    expect(displayBrandCopy('Welcome to Ares Agent!', 'fallback')).toBe('Welcome to Ares Agent!')
  })

  it('paints the welcome wordmark as clawd in the same block font, not HERMES', () => {
    const lines = logo(DARK_THEME.color).map(([, text]) => text)

    expect(lines).toHaveLength(6)
    expect(LOGO_WIDTH).toBe(42)
    expect(lines.join('\n')).not.toContain('██╗  ██╗███████╗')
    expect(lines[0]?.trim()).toBe('██████╗██╗      █████╗ ██╗    ██╗██████╗')
  })
})
