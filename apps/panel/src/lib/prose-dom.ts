/**
 * Shared DOM patching and details state preservation helpers.
 * Used by both stableHTML (full document patch) and blockHTML (per-block patch).
 */

export interface DetailsStateStore {
  has(details: HTMLDetailsElement): boolean;
  get(details: HTMLDetailsElement): boolean | undefined;
  set(details: HTMLDetailsElement, open: boolean): void;
}

export function rememberDetails(node: Node, store: DetailsStateStore): void {
  if (!(node instanceof Element)) return;
  if (node instanceof HTMLDetailsElement) store.set(node, node.open);
  for (const details of node.querySelectorAll("details")) store.set(details, details.open);
}

export function patchChildren(parent: Node, desired: Node, store: DetailsStateStore): void {
  let current = parent.firstChild;
  for (const next of Array.from(desired.childNodes)) {
    if (!current) {
      rememberDetails(next, store);
      parent.appendChild(next);
      continue;
    }
    const sibling = current.nextSibling;
    if (current.nodeType === next.nodeType && (
      !(current instanceof Element) || (next instanceof Element && current.localName === next.localName && current.namespaceURI === next.namespaceURI)
    )) {
      patchNode(current, next, store);
    } else {
      rememberDetails(next, store);
      parent.replaceChild(next, current);
    }
    current = sibling;
  }
  while (current) {
    const sibling = current.nextSibling;
    parent.removeChild(current);
    current = sibling;
  }
}

export function patchNode(current: Node, desired: Node, store: DetailsStateStore): void {
  if (current.isEqualNode(desired)) return;
  if (current instanceof Text && desired instanceof Text) {
    // Replace only the changed range: assigning textContent resets an existing selection.
    const before = current.data, after = desired.data;
    let prefix = 0, suffix = 0;
    while (prefix < before.length && prefix < after.length && before[prefix] === after[prefix]) prefix++;
    while (suffix < before.length - prefix && suffix < after.length - prefix && before[before.length - suffix - 1] === after[after.length - suffix - 1]) suffix++;
    current.replaceData(prefix, before.length - prefix - suffix, after.slice(prefix, after.length - suffix));
    return;
  }
  if (!(current instanceof Element) || !(desired instanceof Element)) {
    current.nodeValue = desired.nodeValue;
    return;
  }
  const details = current instanceof HTMLDetailsElement ? current : null;
  const preserveOpen = details && store.has(details) && details.open !== store.get(details);
  for (const attribute of Array.from(current.attributes)) {
    if (attribute.name === "open" && preserveOpen) continue;
    if (!desired.hasAttribute(attribute.name)) current.removeAttribute(attribute.name);
  }
  for (const attribute of Array.from(desired.attributes)) {
    if (attribute.name === "open" && preserveOpen) continue;
    if (current.getAttribute(attribute.name) !== attribute.value) current.setAttribute(attribute.name, attribute.value);
  }
  if (details) store.set(details, desired.hasAttribute("open"));
  patchChildren(current, desired, store);
}
