import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

// Local static file server, with no API routes or backend proxy.
const assets = new Map([
  ['/', ['index.html', 'text/html; charset=utf-8']],
  ['/index.html', ['index.html', 'text/html; charset=utf-8']],
  ['/styles.css', ['styles.css', 'text/css; charset=utf-8']],
  ['/app.mjs', ['app.mjs', 'text/javascript; charset=utf-8']],
  ['/parameters.mjs', ['parameters.mjs', 'text/javascript; charset=utf-8']],
]);

export function createUiServer() {
  return createServer(async (request, response) => {
    const asset = assets.get((request.url ?? '/').split('?')[0]);
    if (!asset || !['GET', 'HEAD'].includes(request.method)) {
      response.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
      response.end('Not found');
      return;
    }
    try {
      const body = await readFile(new URL(asset[0], import.meta.url));
      response.writeHead(200, { 'Content-Type': asset[1], 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
      response.end(request.method === 'HEAD' ? undefined : body);
    } catch (error) {
      console.error('Cannot serve UI asset:', error.message);
      response.writeHead(500, { 'Content-Type': 'text/plain; charset=utf-8' });
      response.end('Cannot load UI asset');
    }
  });
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  const port = Number(process.env.PORT ?? 3000);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('PORT must be 1–65535');
  const server = createUiServer();
  server.on('error', (error) => { console.error(error.message); process.exitCode = 1; });
  server.listen(port, '127.0.0.1', () => console.log(`UI: http://127.0.0.1:${port}`));
}
