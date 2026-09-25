const NON_TEXT_INPUTS: Record<string, true> = { radio: true, checkbox: true, range: true, button: true };

/** Skróty jednoklawiszowe nie mogą przejmować pisania w polach ani działać pod otwartym modalem. */
export function shouldIgnoreShortcut(event: KeyboardEvent): boolean {
  if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return true;
  const target = event.target as HTMLElement | null;
  if (target) {
    if (target.isContentEditable || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT') return true;
    if (target.tagName === 'INPUT' && !NON_TEXT_INPUTS[(target as HTMLInputElement).type]) return true;
  }
  return Boolean(document.querySelector('.modal-backdrop, [aria-modal="true"]'));
}
