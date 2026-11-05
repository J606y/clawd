const DOMPurify = {
  addHook() {},
  removeHook() {},
  sanitize(html) { return String(html ?? ''); },
  isSupported: false,
};
export default DOMPurify;
