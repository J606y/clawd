/** User-visible product name. Not a package, protocol, env, or wake-word identifier. */
export const PRODUCT_NAME = 'clawd'

/** User-visible CLI name in prompts. Process spawn stays on the internal launcher. */
export const PRODUCT_COMMAND = 'clawd'

const DEFAULT_PRODUCT_NAMES = new Set(['Hermes', 'Hermes Agent'])

/**
 * Map the built-in Hermes product name to clawd.
 * A custom skin name (Ares Agent, TestBot, …) is the user's label and passes through.
 */
export function displayBrandName(agentName: string | undefined, fallback = PRODUCT_NAME): string {
  const value = (agentName ?? '').trim()

  if (!value || DEFAULT_PRODUCT_NAMES.has(value)) {
    return fallback
  }

  return value
}

/** Replace only the default product name inside skin welcome/goodbye copy. */
export function displayBrandCopy(text: string | undefined, fallback: string): string {
  return (text ?? fallback).replaceAll('Hermes Agent', PRODUCT_NAME)
}
