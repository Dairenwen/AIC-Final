export type ThemeMode = 'light' | 'dark'

const THEME_STORAGE_KEY = 'zhiyan.theme'

export function getStoredTheme(): ThemeMode {
  if (typeof window === 'undefined') return 'light'

  try {
    return window.localStorage.getItem(THEME_STORAGE_KEY) === 'dark' ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

export function applyTheme(theme: ThemeMode): void {
  if (typeof document === 'undefined') return

  document.documentElement.dataset.theme = theme
  document.documentElement.style.colorScheme = theme

  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme)
  } catch {
    // Restricted storage should not prevent switching the current theme.
  }
}

export function initializeTheme(): ThemeMode {
  const theme = getStoredTheme()
  applyTheme(theme)
  return theme
}
