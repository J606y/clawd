import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';

const server = await createServer({
  configFile: false,
  server: { middlewareMode: true, hmr: false },
  appType: 'custom'
});

try {
  const { parseAssistantContent, artifactReference } = await server.ssrLoadModule('/src/lib/artifacts.ts');
  const validPath = '/root/hermes-workspace/valid.png';

  await test('reproduce: nested list with code block still discovers attachment outside code', () => {
    const text = `1. item 1\n   \`\`\`\n   foo\n   \`\`\`\n   Here is the image: ${validPath}`;
    const result = parseAssistantContent(text);
    assert.equal(result.artifacts.length, 1);
    assert.equal(result.artifacts[0].source, validPath);
    assert.ok(result.text.includes('1. item 1'));
  });

  await test('reproduce: nested blockquote with code block still discovers attachment outside code', () => {
    const text = `> Note:\n> \`\`\`\n> echo test\n> \`\`\`\n> Attached image: ${validPath}`;
    const result = parseAssistantContent(text);
    assert.equal(result.artifacts.length, 1);
    assert.equal(result.artifacts[0].source, validPath);
    assert.ok(result.text.includes('> Note:'));
  });

  await test('security: nested list or blockquote with code block does NOT discover artifact inside code block', () => {
    const text = `1. item 1\n   \`\`\`\n   ${validPath}\n   \`\`\`\n   Done`;
    const result = parseAssistantContent(text);
    assert.equal(result.artifacts.length, 0);

    const bqText = `> Note:\n> \`\`\`\n> ${validPath}\n> \`\`\`\n> Done`;
    const bqResult = parseAssistantContent(bqText);
    assert.equal(bqResult.artifacts.length, 0);

    const inlineInList = `* item with \`${validPath}\` in codespan`;
    assert.equal(parseAssistantContent(inlineInList).artifacts.length, 0);

    const inlineInQuote = `> quote with \`${validPath}\` in codespan`;
    assert.equal(parseAssistantContent(inlineInQuote).artifacts.length, 0);
  });

  await test('security: deeply nested blockquote and list preserve real attachments outside code while protecting code', () => {
    const validDoc = '/root/hermes-workspace/output.pdf';
    const text = `> 1. outer list\n>    > nested quote\n>    > \`\`\`sh\n>    > cat ${validPath}\n>    > \`\`\`\n>    > outside file: ${validDoc}`;
    const result = parseAssistantContent(text);
    assert.equal(result.artifacts.length, 1);
    assert.equal(result.artifacts[0].source, validDoc);
  });

  await test('html security: html blocks and tags remain protected even when nested', () => {
    const htmlBlock = `> <div>\n> ${validPath}\n> </div>`;
    assert.equal(parseAssistantContent(htmlBlock).artifacts.length, 0);

    const text = `\`<img src="${validPath}">\``;
    assert.equal(parseAssistantContent(text).artifacts.length, 0);

    const inlineHtml = `<span>${validPath}</span>`;
    assert.equal(parseAssistantContent(inlineHtml).artifacts.length, 0);
  });

  await test('deduplication: duplicate paths in nested and unnested structures yield exactly one artifact', () => {
    const text = `Here is ${validPath}\n\n> In quote: ${validPath}\n> \`\`\`\n> fake\n> \`\`\``;
    const result = parseAssistantContent(text);
    assert.equal(result.artifacts.length, 1);
    assert.equal(result.artifacts[0].source, validPath);
  });

  await test('external real attachments and markdown links preserved in nested lists and blockquotes', () => {
    const text = `> - Item 1\n>   [下载说明书](${validPath})\n> - Item 2\n>   MEDIA:"/root/.hermes/images/sub.png"`;
    const result = parseAssistantContent(text);
    assert.equal(result.artifacts.length, 2);
    assert.equal(result.artifacts[0].source, validPath);
    assert.equal(result.artifacts[1].source, '/root/.hermes/images/sub.png');
  });

  await test('table with code block and plain attachment', () => {
    const text = `| 工具 | 说明 |\n| --- | --- |\n| \`run ${validPath}\` | 参见 ${validPath} |`;
    const result = parseAssistantContent(text);
    assert.equal(result.artifacts.length, 1);
    assert.equal(result.artifacts[0].source, validPath);
  });
} finally {
  await server.close();
}
