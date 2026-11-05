import { spawn } from 'node:child_process';

export function runBrowser(script, name, timeoutMs = 120000) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [new URL(script, import.meta.url).pathname, name], { stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '';
    child.stdout.on('data', (chunk) => { out += chunk; });
    child.stderr.on('data', (chunk) => { out += chunk; });
    const timer = setTimeout(() => { child.kill('SIGTERM'); reject(new Error(`browser case timed out\n${out}`)); }, timeoutMs);
    child.on('exit', (code) => {
      clearTimeout(timer);
      if (code === 0) resolve(out);
      else reject(new Error(out || `browser case exited ${code}`));
    });
  });
}
